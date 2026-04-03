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

# Estados
AGUARDA_PRINT_BOASVINDAS = 1

# Banco em memória + SQLite
DB_PATH = "naantips.db"

# ─── SERVIDOR HTTP ─────────────────────────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"NAAN Tips Bot ok!")
    def log_message(self, format, *args):
        pass

def iniciar_http():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    t = threading.Thread(target=server.serve_forever)
    t.daemon = True
    t.start()
    logger.info(f"HTTP rodando na porta {PORT}")

# ─── BANCO DE DADOS ────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS usuarios (
        user_id INTEGER PRIMARY KEY,
        nome TEXT, username TEXT,
        etapa TEXT DEFAULT 'inicio',
        status TEXT DEFAULT 'pendente',
        file_bv TEXT, file_dep TEXT,
        criado_em TEXT, atualizado_em TEXT
    )""")
    con.commit()
    con.close()

def salvar(user_id, nome=None, username=None, etapa=None, status=None, file_bv=None, file_dep=None):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("SELECT user_id FROM usuarios WHERE user_id=?", (user_id,))
    if not cur.fetchone():
        cur.execute("INSERT INTO usuarios VALUES (?,?,?,?,?,?,?,?,?)",
            (user_id, nome, username, etapa or "inicio", status or "pendente", file_bv, file_dep, agora, agora))
    else:
        campos, vals = [], []
        if nome:     campos.append("nome=?");     vals.append(nome)
        if username: campos.append("username=?"); vals.append(username)
        if etapa:    campos.append("etapa=?");    vals.append(etapa)
        if status:   campos.append("status=?");   vals.append(status)
        if file_bv:  campos.append("file_bv=?");  vals.append(file_bv)
        if file_dep: campos.append("file_dep=?"); vals.append(file_dep)
        campos.append("atualizado_em=?"); vals.append(agora)
        vals.append(user_id)
        cur.execute(f"UPDATE usuarios SET {','.join(campos)} WHERE user_id=?", vals)
    con.commit()
    con.close()

def buscar(user_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT user_id,nome,username,etapa,status FROM usuarios WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    if row:
        return {"user_id":row[0],"nome":row[1],"username":row[2],"etapa":row[3],"status":row[4]}
    return {}

def todos():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT user_id,nome,username,etapa,status,criado_em FROM usuarios ORDER BY criado_em DESC")
    rows = cur.fetchall()
    con.close()
    return rows

# ─── HELPER ────────────────────────────────────────────────────────────────────
def link_afiliado(user_id):
    return f"{AFILIADO_BASE}&subid={user_id}"

async def avisar_admin(context, user_id, tipo, file_id):
    u = buscar(user_id)
    tipo_label = "📋 Boas-vindas (Cadastro)" if tipo == "bv" else f"💰 Depósito R$ {VALOR_MINIMO}"
    caption = (
        f"🔔 *Novo print para aprovação!*\n\n"
        f"{tipo_label}\n\n"
        f"👤 {u.get('nome','?')} | @{u.get('username','?')}\n"
        f"🆔 `{user_id}`\n\n"
        f"👇 Decida:"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Aprovar", callback_data=f"ok:{user_id}:{tipo}"),
        InlineKeyboardButton("❌ Rejeitar", callback_data=f"rej:{user_id}:{tipo}"),
    ]])
    try:
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=file_id,
            caption=caption, parse_mode="Markdown", reply_markup=kb)
    except Exception as e:
        logger.error(f"Erro admin: {e}")

# ─── HANDLERS ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    salvar(user.id, nome=user.full_name, username=user.username or "", etapa="inicio")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sim, já tenho conta", callback_data="sim")],
        [InlineKeyboardButton("❌ Não tenho conta ainda", callback_data="nao")],
    ])
    await update.message.reply_text(
        f"Fala *{user.first_name}* 👋, suave?\n\n"
        "Vi que você tem interesse em entrar no *Grupo de ODD Altas NAAN Tips* 📈, certo?\n\n"
        "Me responde aqui: *Você já possui conta na JonBet?*",
        parse_mode="Markdown",
        reply_markup=kb
    )

async def botao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = query.data

    if data == "sim":
        salvar(user.id, etapa="aguarda_bv")
        await query.edit_message_text(
            "Show! 😎\n\n"
            "*Passo 1 de 2 — Print de cadastro*\n\n"
            "Me manda um print da *tela inicial da sua conta* na JonBet "
            "(app ou site) pra confirmar que a conta existe ✅",
            parse_mode="Markdown"
        )

    elif data == "nao":
        salvar(user.id, etapa="aguarda_bv")
        link = link_afiliado(user.id)
        await query.edit_message_text(
            "Tranquilo, é rapidinho! 🚀\n\n"
            "Faz o cadastro *obrigatoriamente por esse link* 👇\n"
            f"{link}\n\n"
            "Depois volta aqui e me manda um *print da tela inicial* da JonBet ✅\n\n"
            "_Esse é o Passo 1 de 2!_ 😉",
            parse_mode="Markdown"
        )

    elif data.startswith("ok:") or data.startswith("rej:"):
        if update.effective_user.id != ADMIN_ID:
            await query.answer("Sem permissão.", show_alert=True)
            return

        partes = data.split(":")
        acao, user_id, tipo = partes[0], int(partes[1]), partes[2]
        caption = query.message.caption or ""

        if acao == "ok":
            if tipo == "bv":
                salvar(user_id, etapa="aguarda_dep")
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
                caption += "\n\n✅ Aprovado — aguardando depósito."
            else:
                salvar(user_id, etapa="aprovado", status="aprovado")
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "🎉 *Tudo certo, você foi aprovado!*\n\n"
                        "Seja bem-vindo ao *Grupo de ODD Altas NAAN Tips* 📈\n\n"
                        f"👉 Acessa aqui: {GRUPO_LINK}\n\n"
                        "Bora lucrar! 🚀😎"
                    ),
                    parse_mode="Markdown"
                )
                caption += "\n\n✅ APROVADO — link enviado!"

        else:  # rejeitar
            if tipo == "bv":
                salvar(user_id, etapa="aguarda_bv")
                instrucao = "a tela inicial da JonBet esteja visível"
            else:
                salvar(user_id, etapa="aguarda_dep")
                instrucao = f"o saldo de R$ {VALOR_MINIMO},00 apareça claramente"

            tipo_label = "cadastro" if tipo == "bv" else "depósito"
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"⚠️ Não consegui verificar seu print de *{tipo_label}*.\n\n"
                    "Manda de novo com:\n"
                    "• Imagem nítida\n"
                    "• Nome JonBet visível\n"
                    f"• {instrucao}\n\n"
                    "Qualquer dúvida é só falar! 🙏"
                ),
                parse_mode="Markdown"
            )
            caption += "\n\n❌ Rejeitado — usuário notificado."

        try:
            await query.edit_message_caption(caption=caption, parse_mode="Markdown")
        except Exception:
            pass

async def foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    dados = buscar(user.id)
    etapa = dados.get("etapa", "")

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    else:
        await update.message.reply_text("Manda como *imagem* 📸", parse_mode="Markdown")
        return

    if etapa == "aguarda_bv":
        salvar(user.id, etapa="bv_analise", file_bv=file_id)
        await avisar_admin(context, user.id, "bv", file_id)
        await update.message.reply_text(
            "✅ *Print recebido!*\n\nAguarda, estou verificando 🙏\n_Assim que confirmar, te chamo pro próximo passo!_",
            parse_mode="Markdown"
        )
    elif etapa == "aguarda_dep":
        salvar(user.id, etapa="dep_analise", file_dep=file_id)
        await avisar_admin(context, user.id, "dep", file_id)
        await update.message.reply_text(
            "✅ *Comprovante recebido!*\n\nEstou verificando, em breve te libero! 🏆",
            parse_mode="Markdown"
        )
    elif etapa in ("bv_analise", "dep_analise"):
        await update.message.reply_text("⏳ Seu print ainda está em análise, aguarda!")
    else:
        await update.message.reply_text("Manda /start pra começar 👋")

async def texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dados = buscar(update.effective_user.id)
    etapa = dados.get("etapa", "")
    if etapa == "aguarda_dep":
        await update.message.reply_text(f"Me manda o *print do depósito de R$ {VALOR_MINIMO},00* 📸", parse_mode="Markdown")
    elif etapa in ("bv_analise", "dep_analise"):
        await update.message.reply_text("⏳ Seu print está em análise, aguarda!")
    else:
        await update.message.reply_text("Manda /start pra começar 👋")

async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    rows = todos()
    if not rows:
        await update.message.reply_text("Nenhum usuário ainda.")
        return
    emojis = {"aprovado":"✅","pendente":"⏳","rejeitado":"❌"}
    txt = "👥 *Usuários:*\n\n"
    for r in rows[:20]:
        e = emojis.get(r[4],"⏳")
        txt += f"{e} {r[1]} | @{r[2]} | `{r[0]}`\n   _{r[3]} | {r[5]}_\n\n"
    txt += f"_Total: {len(rows)}_"
    await update.message.reply_text(txt, parse_mode="Markdown")

# ─── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN não configurado!")

    init_db()
    iniciar_http()
    logger.info("✅ DB e HTTP iniciados!")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("lista", lista))
    app.add_handler(CallbackQueryHandler(botao))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, foto))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, texto))

    logger.info("🤖 Bot NAAN Tips iniciado!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
