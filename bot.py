import logging
import os
import sqlite3
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── CONFIGURAÇÕES ────────────────────────────────────────────────────────────
BOT_TOKEN     = os.environ.get("BOT_TOKEN", "")
ADMIN_ID      = int(os.environ.get("ADMIN_ID", "0"))
GRUPO_LINK    = os.environ.get("GRUPO_LINK", "")
AFILIADO_BASE = os.environ.get("AFILIADO_BASE", "")
VALOR_MINIMO  = os.environ.get("VALOR_MINIMO", "20")
PORT          = int(os.environ.get("PORT", "8080"))

# Estados da conversa
(PERGUNTA_CONTA, AGUARDA_PRINT_BOASVINDAS) = range(2)

# ─── SERVIDOR HTTP (mantém o Render acordado) ─────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"NAAN Tips Bot rodando!")
    def log_message(self, format, *args):
        pass  # silencia logs do HTTP

def iniciar_servidor_http():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    logger.info(f"✅ Servidor HTTP rodando na porta {PORT}")

# ─── BANCO DE DADOS ───────────────────────────────────────────────────────────
DB_PATH = "naantips.db"

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            user_id       INTEGER PRIMARY KEY,
            nome          TEXT,
            username      TEXT,
            etapa         TEXT DEFAULT 'inicio',
            status        TEXT DEFAULT 'pendente',
            file_bv       TEXT,
            file_dep      TEXT,
            criado_em     TEXT,
            atualizado_em TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS eventos (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER,
            username  TEXT,
            evento    TEXT,
            criado_em TEXT
        )
    """)
    con.commit()
    con.close()

def upsert_usuario(user_id, nome, username, etapa=None, status=None, file_bv=None, file_dep=None):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("SELECT user_id FROM usuarios WHERE user_id=?", (user_id,))
    existe = cur.fetchone()
    if not existe:
        cur.execute("""
            INSERT INTO usuarios (user_id, nome, username, etapa, status, file_bv, file_dep, criado_em, atualizado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, nome, username, etapa or "inicio", status or "pendente", file_bv, file_dep, agora, agora))
    else:
        campos, valores = [], []
        if etapa    is not None: campos.append("etapa=?");    valores.append(etapa)
        if status   is not None: campos.append("status=?");   valores.append(status)
        if file_bv  is not None: campos.append("file_bv=?");  valores.append(file_bv)
        if file_dep is not None: campos.append("file_dep=?"); valores.append(file_dep)
        if nome:     campos.append("nome=?");     valores.append(nome)
        if username: campos.append("username=?"); valores.append(username)
        campos.append("atualizado_em=?")
        valores.append(agora)
        valores.append(user_id)
        cur.execute(f"UPDATE usuarios SET {', '.join(campos)} WHERE user_id=?", valores)
    con.commit()
    con.close()

def get_usuario(user_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT user_id, nome, username, etapa, status FROM usuarios WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    if row:
        return {"user_id": row[0], "nome": row[1], "username": row[2], "etapa": row[3], "status": row[4]}
    return None

def log_evento(user_id, username, evento):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("INSERT INTO eventos (user_id, username, evento, criado_em) VALUES (?,?,?,?)",
                (user_id, username, evento, agora))
    con.commit()
    con.close()

def get_todos_usuarios():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT user_id, nome, username, etapa, status, criado_em FROM usuarios ORDER BY criado_em DESC")
    rows = cur.fetchall()
    con.close()
    return rows

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def link_afiliado(user_id: int) -> str:
    return f"{AFILIADO_BASE}&subid={user_id}"

async def notificar_admin(context, user_id: int, tipo: str, file_id: str):
    user     = get_usuario(user_id)
    username = user.get("username", "?") if user else "?"
    nome     = user.get("nome", "?")     if user else "?"
    tipo_label = "📋 Print de Boas-vindas (Cadastro)" if tipo == "boasvindas" else f"💰 Print de Depósito (R$ {VALOR_MINIMO})"
    caption = (
        f"🔔 *Novo print aguardando aprovação!*\n\n"
        f"{tipo_label}\n\n"
        f"👤 Nome: {nome}\n"
        f"📛 Username: @{username}\n"
        f"🆔 ID: `{user_id}`\n\n"
        f"👇 O que deseja fazer?"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Aprovar", callback_data=f"aprovar:{user_id}:{tipo}"),
        InlineKeyboardButton("❌ Rejeitar", callback_data=f"rejeitar:{user_id}:{tipo}"),
    ]])
    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID, photo=file_id,
            caption=caption, parse_mode="Markdown", reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Erro ao notificar admin: {e}")

# ─── FLUXO DO USUÁRIO ─────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    upsert_usuario(user.id, user.full_name, user.username or "sem_username", etapa="inicio", status="pendente")
    log_evento(user.id, user.username or "", "iniciou conversa")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sim, já tenho conta", callback_data="tem_conta")],
        [InlineKeyboardButton("❌ Não tenho conta ainda", callback_data="nao_tem_conta")],
    ])
    await update.message.reply_text(
        f"Fala *{user.first_name}* 👋, suave?\n\n"
        "Vi que você tem interesse em entrar no *Grupo de ODD Altas NAAN Tips* 📈, certo?\n\n"
        "Me responde aqui: *Você já possui conta na JonBet?*\n\n"
        "_Cadastre-se agora e faça parte do grupo!_",
        parse_mode="Markdown", reply_markup=keyboard
    )
    return PERGUNTA_CONTA

async def resposta_tem_conta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    upsert_usuario(query.from_user.id, query.from_user.full_name, query.from_user.username or "", etapa="aguarda_boasvindas")
    await query.edit_message_text(
        "Show! 😎\n\n*Passo 1 de 2 — Print de cadastro*\n\n"
        "Me manda um print da *tela inicial da sua conta* na JonBet "
        "(pode ser pelo app ou site) pra eu confirmar que a conta existe ✅",
        parse_mode="Markdown"
    )
    return AGUARDA_PRINT_BOASVINDAS

async def resposta_nao_tem_conta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = query.from_user
    upsert_usuario(user.id, user.full_name, user.username or "", etapa="aguarda_boasvindas")
    link = link_afiliado(user.id)
    await query.edit_message_text(
        "Tranquilo, é rapidinho! 🚀\n\n"
        "Faz o cadastro *obrigatoriamente por esse link* 👇\n"
        f"{link}\n\n"
        "Depois que criar a conta, volta aqui e me manda um *print da tela inicial* "
        "da JonBet (app ou site) ✅\n\n"
        "_Esse é o Passo 1 de 2!_ 😉",
        parse_mode="Markdown"
    )
    return AGUARDA_PRINT_BOASVINDAS

async def receber_print_boasvindas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    else:
        await update.message.reply_text("Por favor, manda como *imagem* 📸", parse_mode="Markdown")
        return AGUARDA_PRINT_BOASVINDAS
    upsert_usuario(user.id, user.full_name, user.username or "", etapa="boasvindas_em_analise", file_bv=file_id)
    log_evento(user.id, user.username or "", "print boas-vindas enviado")
    await notificar_admin(context, user.id, "boasvindas", file_id)
    await update.message.reply_text(
        "✅ *Print recebido!*\n\nEstou verificando tudo, aguarda um instante 🙏\n\n"
        "_Assim que confirmar, te chamo pro próximo passo!_",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def receber_print_deposito(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    else:
        await update.message.reply_text("Por favor, manda como *imagem* 📸", parse_mode="Markdown")
        return
    upsert_usuario(user.id, user.full_name, user.username or "", etapa="deposito_em_analise", file_dep=file_id)
    log_evento(user.id, user.username or "", "print depósito enviado")
    await notificar_admin(context, user.id, "deposito", file_id)
    await update.message.reply_text(
        "✅ *Comprovante recebido!*\n\nEstou verificando aqui, em breve te libero no grupo! 🏆\n\n_Quase lá!_ 😎",
        parse_mode="Markdown"
    )

# ─── PAINEL ADMIN ─────────────────────────────────────────────────────────────
async def callback_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID:
        await query.answer("Sem permissão.", show_alert=True)
        return
    await query.answer()
    acao, user_id_str, tipo = query.data.split(":")
    user_id  = int(user_id_str)
    user     = get_usuario(user_id)
    username = user.get("username", "?") if user else "?"
    novo_caption = query.message.caption or ""

    if acao == "aprovar":
        if tipo == "boasvindas":
            upsert_usuario(user_id, None, None, etapa="aguarda_deposito")
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "✅ *Cadastro confirmado!* Maneiro demais 🔥\n\n"
                        f"*Passo 2 de 2 — Depósito*\n\n"
                        f"Agora deposita *R$ {VALOR_MINIMO},00* na JonBet "
                        "e me manda o *print com o saldo na conta* 💰\n\n"
                        "_Assim que confirmar, você já entra no grupo de forma vitalícia!_ 🏆"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(e)
            novo_caption += "\n\n✅ *Aprovado — aguardando depósito.*"
            log_evento(user_id, username, "boas-vindas APROVADO")
        elif tipo == "deposito":
            upsert_usuario(user_id, None, None, etapa="aprovado", status="aprovado")
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "🎉 *Tudo certo, você foi aprovado!*\n\n"
                        "Seja bem-vindo ao *Grupo de ODD Altas NAAN Tips* 📈\n\n"
                        f"👉 Acessa aqui: {GRUPO_LINK}\n\n"
                        "Vem leve e bora alavançar! 🚀😎"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(e)
            novo_caption += "\n\n✅ *APROVADO — link do grupo enviado!*"
            log_evento(user_id, username, "depósito APROVADO — link enviado")

    elif acao == "rejeitar":
        if tipo == "boasvindas":
            instrucao = "a tela inicial da JonBet esteja visível e legível"
            upsert_usuario(user_id, None, None, etapa="aguarda_boasvindas")
        else:
            instrucao = f"o saldo de R$ {VALOR_MINIMO},00 apareça claramente na tela"
            upsert_usuario(user_id, None, None, etapa="aguarda_deposito")
        tipo_label = "cadastro" if tipo == "boasvindas" else "depósito"
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"⚠️ Ei, não consegui verificar seu print de *{tipo_label}*.\n\n"
                    "Manda de novo caprichando nisso:\n"
                    "• Imagem nítida e sem cortes\n"
                    "• Nome da JonBet visível\n"
                    f"• Certifique-se que {instrucao}\n\n"
                    "Qualquer dúvida é só falar! 🙏"
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(e)
        novo_caption += "\n\n❌ *Rejeitado — usuário notificado para reenviar.*"
        log_evento(user_id, username, f"{tipo} REJEITADO")

    try:
        await query.edit_message_caption(caption=novo_caption, parse_mode="Markdown")
    except Exception:
        pass

async def lista_usuarios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    rows = get_todos_usuarios()
    if not rows:
        await update.message.reply_text("Nenhum usuário cadastrado ainda.")
        return
    status_emoji = {"aprovado": "✅", "pendente": "⏳", "rejeitado": "❌"}
    texto = "👥 *Lista de usuários:*\n\n"
    for r in rows[:20]:
        emoji = status_emoji.get(r[4], "⏳")
        texto += f"{emoji} {r[1]} | @{r[2]} | `{r[0]}`\n"
        texto += f"   _Etapa: {r[3]} | {r[5]}_\n\n"
    texto += f"_Total: {len(rows)} usuários_"
    await update.message.reply_text(texto, parse_mode="Markdown")

async def foto_generica(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    dados = get_usuario(user.id)
    etapa = dados.get("etapa", "") if dados else ""
    if etapa == "aguarda_deposito":
        await receber_print_deposito(update, context)
    elif etapa in ("boasvindas_em_analise", "deposito_em_analise"):
        await update.message.reply_text("⏳ Seu print ainda está em análise, aguarda um pouquinho!")
    else:
        await update.message.reply_text("Manda /start pra começar 👋")

async def texto_generico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dados = get_usuario(update.effective_user.id)
    etapa = dados.get("etapa", "") if dados else ""
    if etapa in ("boasvindas_em_analise", "deposito_em_analise"):
        await update.message.reply_text("⏳ Seu print está em análise, aguarda!")
    elif etapa == "aguarda_deposito":
        await update.message.reply_text(f"Me manda o *print do depósito de R$ {VALOR_MINIMO},00* 📸", parse_mode="Markdown")
    else:
        await update.message.reply_text("Manda /start pra começar 👋")

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Ok! Se mudar de ideia é só mandar /start 😉")
    return ConversationHandler.END

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN não configurado!")
    if not ADMIN_ID:
        raise ValueError("ADMIN_ID não configurado!")

    init_db()
    logger.info("✅ Banco de dados iniciado!")

    iniciar_servidor_http()

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            PERGUNTA_CONTA: [
                CallbackQueryHandler(resposta_tem_conta,     pattern="^tem_conta$"),
                CallbackQueryHandler(resposta_nao_tem_conta, pattern="^nao_tem_conta$"),
            ],
            AGUARDA_PRINT_BOASVINDAS: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, receber_print_boasvindas),
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
        allow_reentry=True,
        per_message=False,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("lista", lista_usuarios))
    app.add_handler(CallbackQueryHandler(callback_admin, pattern="^(aprovar|rejeitar):"))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, foto_generica))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, texto_generico))

    logger.info("🤖 Bot NAAN Tips iniciado com sucesso!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
