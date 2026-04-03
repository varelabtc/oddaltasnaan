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
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN     = os.environ.get("BOT_TOKEN", "")
ADMIN_ID      = int(os.environ.get("ADMIN_ID", "0"))
GRUPO_LINK    = os.environ.get("GRUPO_LINK", "")
AFILIADO_BASE = os.environ.get("AFILIADO_BASE", "")
VALOR_MINIMO  = os.environ.get("VALOR_MINIMO", "20")
PORT          = int(os.environ.get("PORT", "8080"))
DB_PATH       = "naantips.db"

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *a): pass

def iniciar_http():
    t = threading.Thread(target=HTTPServer(("0.0.0.0", PORT), H).serve_forever)
    t.daemon = True
    t.start()

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS u (
        id INTEGER PRIMARY KEY, nome TEXT, username TEXT,
        etapa TEXT DEFAULT 'inicio', status TEXT DEFAULT 'pendente',
        fbv TEXT, fdep TEXT, criado TEXT, atualizado TEXT)""")
    con.commit(); con.close()

def salvar(uid, nome=None, username=None, etapa=None, status=None, fbv=None, fdep=None):
    con = sqlite3.connect(DB_PATH)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = con.execute("SELECT id FROM u WHERE id=?", (uid,)).fetchone()
    if not row:
        con.execute("INSERT INTO u VALUES (?,?,?,?,?,?,?,?,?)",
            (uid, nome, username, etapa or "inicio", status or "pendente", fbv, fdep, now, now))
    else:
        f, v = [], []
        for col, val in [("nome",nome),("username",username),("etapa",etapa),
                         ("status",status),("fbv",fbv),("fdep",fdep)]:
            if val is not None: f.append(f"{col}=?"); v.append(val)
        f.append("atualizado=?"); v.append(now); v.append(uid)
        con.execute(f"UPDATE u SET {','.join(f)} WHERE id=?", v)
    con.commit(); con.close()

def buscar(uid):
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT id,nome,username,etapa,status FROM u WHERE id=?", (uid,)).fetchone()
    con.close()
    return {"id":row[0],"nome":row[1],"username":row[2],"etapa":row[3],"status":row[4]} if row else {}

def todos():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT id,nome,username,etapa,status,criado FROM u ORDER BY criado DESC").fetchall()
    con.close(); return rows

def link_af(uid): return f"{AFILIADO_BASE}&subid={uid}"

async def avisar_admin(ctx, uid, tipo, fid):
    u = buscar(uid)
    label = "Boas-vindas" if tipo == "bv" else f"Deposito R$ {VALOR_MINIMO}"
    cap = f"Novo print!\n\n{label}\nNome: {u.get('nome','?')}\nUser: @{u.get('username','?')}\nID: {uid}"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Aprovar", callback_data=f"ok:{uid}:{tipo}"),
        InlineKeyboardButton("Rejeitar", callback_data=f"rej:{uid}:{tipo}"),
    ]])
    try:
        await ctx.bot.send_photo(chat_id=ADMIN_ID, photo=fid, caption=cap, reply_markup=kb)
    except Exception as e:
        logger.error(f"Admin error: {e}")

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    salvar(u.id, nome=u.full_name, username=u.username or "", etapa="inicio")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Sim, ja tenho conta", callback_data="sim")],
        [InlineKeyboardButton("Nao tenho conta ainda", callback_data="nao")],
    ])
    await update.message.reply_text(
        f"Fala {u.first_name}, suave?\n\n"
        "Vi que voce tem interesse em entrar no Grupo de ODD Altas NAAN Tips, certo?\n\n"
        "Voce ja possui conta na JonBet?",
        reply_markup=kb
    )

async def botao(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    u = q.from_user
    d = q.data

    if d == "sim":
        salvar(u.id, etapa="aguarda_bv")
        await q.edit_message_text(
            "Show!\n\nPasso 1 de 2 - Print de cadastro\n\n"
            "Me manda um print da tela inicial da sua conta na JonBet "
            "(app ou site) pra confirmar que a conta existe"
        )

    elif d == "nao":
        salvar(u.id, etapa="aguarda_bv")
        link = link_af(u.id)
        await q.edit_message_text(
            f"Tranquilo, e rapidinho!\n\n"
            f"Faz o cadastro obrigatoriamente por esse link:\n{link}\n\n"
            f"Depois volta aqui e me manda um print da tela inicial da JonBet\n\n"
            f"Esse e o Passo 1 de 2!"
        )

    elif d.startswith("ok:") or d.startswith("rej:"):
        if update.effective_user.id != ADMIN_ID:
            await q.answer("Sem permissao.", show_alert=True)
            return
        parts = d.split(":")
        acao, uid, tipo = parts[0], int(parts[1]), parts[2]
        cap = (q.message.caption or "") + "\n\n"

        if acao == "ok":
            if tipo == "bv":
                salvar(uid, etapa="aguarda_dep")
                await ctx.bot.send_message(chat_id=uid, text=(
                    f"Cadastro confirmado! Maneiro!\n\n"
                    f"Passo 2 de 2 - Deposito\n\n"
                    f"Agora deposita R$ {VALOR_MINIMO},00 na JonBet "
                    f"e me manda o print com o saldo na conta\n\n"
                    f"Assim que confirmar, voce ja entra no grupo de forma vitalicia!"
                ))
                cap += "Aprovado - aguardando deposito."
            else:
                salvar(uid, etapa="aprovado", status="aprovado")
                await ctx.bot.send_message(chat_id=uid, text=(
                    f"Tudo certo, voce foi aprovado!\n\n"
                    f"Seja bem-vindo ao Grupo de ODD Altas NAAN Tips!\n\n"
                    f"Acessa aqui: {GRUPO_LINK}\n\nBora lucrar!"
                ))
                cap += "APROVADO - link enviado!"
        else:
            instrucao = "tela inicial da JonBet visivel" if tipo == "bv" else f"saldo de R$ {VALOR_MINIMO},00 claro"
            salvar(uid, etapa="aguarda_bv" if tipo == "bv" else "aguarda_dep")
            await ctx.bot.send_message(chat_id=uid, text=(
                f"Nao consegui verificar seu print.\n\n"
                f"Manda de novo com:\n- Imagem nitida\n- Nome JonBet visivel\n- {instrucao}\n\n"
                f"Qualquer duvida e so falar!"
            ))
            cap += "Rejeitado - usuario notificado."

        try:
            await q.edit_message_caption(caption=cap)
        except Exception:
            pass

async def foto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    dados = buscar(u.id)
    etapa = dados.get("etapa", "")
    fid = update.message.photo[-1].file_id if update.message.photo else (
          update.message.document.file_id if update.message.document else None)
    if not fid:
        await update.message.reply_text("Manda como imagem!")
        return
    if etapa == "aguarda_bv":
        salvar(u.id, etapa="bv_analise", fbv=fid)
        await avisar_admin(ctx, u.id, "bv", fid)
        await update.message.reply_text("Print recebido! Aguarda, estou verificando. Assim que confirmar, te chamo pro proximo passo!")
    elif etapa == "aguarda_dep":
        salvar(u.id, etapa="dep_analise", fdep=fid)
        await avisar_admin(ctx, u.id, "dep", fid)
        await update.message.reply_text("Comprovante recebido! Estou verificando, em breve te libero no grupo!")
    elif etapa in ("bv_analise", "dep_analise"):
        await update.message.reply_text("Seu print ainda esta em analise, aguarda!")
    else:
        await update.message.reply_text("Manda /start pra comecar!")

async def texto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    dados = buscar(update.effective_user.id)
    etapa = dados.get("etapa", "")
    if etapa == "aguarda_dep":
        await update.message.reply_text(f"Me manda o print do deposito de R$ {VALOR_MINIMO},00!")
    elif etapa in ("bv_analise", "dep_analise"):
        await update.message.reply_text("Seu print esta em analise, aguarda!")
    else:
        await update.message.reply_text("Manda /start pra comecar!")

async def lista(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    rows = todos()
    if not rows:
        await update.message.reply_text("Nenhum usuario ainda.")
        return
    e = {"aprovado":"OK","pendente":"...","rejeitado":"X"}
    txt = "Usuarios:\n\n" + "\n".join(
        f"{e.get(r[4],'?')} {r[1]} @{r[2]} {r[0]} - {r[3]}" for r in rows[:20]
    ) + f"\n\nTotal: {len(rows)}"
    await update.message.reply_text(txt)

def main():
    if not BOT_TOKEN: raise ValueError("BOT_TOKEN nao configurado!")
    init_db(); iniciar_http()
    logger.info("DB e HTTP ok!")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("lista", lista))
    app.add_handler(CallbackQueryHandler(botao))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, foto))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, texto))
    logger.info("Bot NAAN Tips iniciado!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
