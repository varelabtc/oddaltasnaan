import logging
import os
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
BOT_TOKEN     = os.environ.get("BOT_TOKEN", "SEU_TOKEN_AQUI")
ADMIN_ID      = int(os.environ.get("ADMIN_ID", "0"))
GRUPO_LINK    = os.environ.get("GRUPO_LINK", "https://t.me/SEU_GRUPO")
AFILIADO_BASE = os.environ.get("AFILIADO_BASE", "https://record.sportingbet.com/visit/?bta=SEU_ID")
VALOR_MINIMO  = os.environ.get("VALOR_MINIMO", "30")

# Estados da conversa
(PERGUNTA_CONTA, AGUARDA_PRINT_BOASVINDAS) = range(2)

# Banco em memória
usuarios: dict[int, dict] = {}

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def link_afiliado(user_id: int) -> str:
    return f"{AFILIADO_BASE}&brand=sportingbet&subid={user_id}"

def salvar_log(user_id: int, username: str, evento: str):
    os.makedirs("logs", exist_ok=True)
    with open("logs/eventos.txt", "a", encoding="utf-8") as f:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{ts}] user_id={user_id} @{username} | {evento}\n")

async def notificar_admin(context, user_id: int, tipo: str, file_id: str):
    user = usuarios.get(user_id, {})
    username = user.get("username", "?")
    nome = user.get("nome", "?")
    tipo_label = "📋 Boas-vindas (cadastro)" if tipo == "boasvindas" else f"💰 Depósito R$ {VALOR_MINIMO}"

    caption = (
        f"🔔 *Novo print para revisão*\n\n"
        f"{tipo_label}\n"
        f"👤 {nome} | @{username}\n"
        f"🆔 `{user_id}`\n\n"
        f"👇 *Decida abaixo:*"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Aprovar", callback_data=f"aprovar:{user_id}:{tipo}"),
        InlineKeyboardButton("❌ Rejeitar", callback_data=f"rejeitar:{user_id}:{tipo}"),
    ]])

    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=file_id,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Erro ao notificar admin: {e}")

# ─── FLUXO DO USUÁRIO ─────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    usuarios[user.id] = {
        "username": user.username or "sem_username",
        "nome": user.full_name,
        "etapa": "inicio",
    }
    salvar_log(user.id, user.username or "", "iniciou conversa")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sim, já tenho conta", callback_data="tem_conta")],
        [InlineKeyboardButton("❌ Não tenho conta ainda", callback_data="nao_tem_conta")],
    ])
    await update.message.reply_text(
        f"Fala *{user.first_name}* 👋, suave?\n\n"
        "Vi que você tem interesse em entrar no grupo de *ODD altas do Careca* 👨🏽‍🦲, certo?\n\n"
        "Me responde aqui: *Você já possui conta na SportingBet?*",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    return PERGUNTA_CONTA

async def resposta_tem_conta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    usuarios[query.from_user.id]["etapa"] = "aguarda_boasvindas"
    await query.edit_message_text(
        "Show! 😎\n\n"
        "*Passo 1 de 2:* Me manda um print da tela inicial da sua conta na SportingBet "
        "(app ou site) pra confirmar o cadastro ✅",
        parse_mode="Markdown"
    )
    return AGUARDA_PRINT_BOASVINDAS

async def resposta_nao_tem_conta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = query.from_user
    usuarios[user.id]["etapa"] = "aguarda_boasvindas"
    link = link_afiliado(user.id)
    await query.edit_message_text(
        "Sem problema! É rapidinho 🚀\n\n"
        "A *SportingBet* é uma das maiores casas do mercado, regulamentada e patrocinadora "
        "do Palmeiras e da Conmebol Libertadores 🏆\n\n"
        f"👉 *Faz o cadastro por esse link:*\n{link}\n\n"
        "Depois de criar a conta, me manda um *print da tela de boas-vindas* da SportingBet ✅\n\n"
        "_Esse é o Passo 1 de 2!_",
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

    usuarios[user.id]["file_boasvindas"] = file_id
    usuarios[user.id]["etapa"] = "boasvindas_em_analise"
    salvar_log(user.id, user.username or "", "print boas-vindas enviado")
    await notificar_admin(context, user.id, "boasvindas", file_id)
    await update.message.reply_text(
        "✅ *Print recebido!*\n\nAguarda um instante, estou verificando tudo 🙏",
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

    usuarios[user.id]["file_deposito"] = file_id
    usuarios[user.id]["etapa"] = "deposito_em_analise"
    salvar_log(user.id, user.username or "", "print depósito enviado")
    await notificar_admin(context, user.id, "deposito", file_id)
    await update.message.reply_text(
        "✅ *Comprovante recebido!*\n\nEstou verificando. Em breve te libero no grupo! 🏆",
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
    user_id = int(user_id_str)
    username = usuarios.get(user_id, {}).get("username", "?")

    if acao == "aprovar":
        if tipo == "boasvindas":
            usuarios[user_id]["etapa"] = "aguarda_deposito"
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "✅ *Cadastro confirmado!*\n\n"
                        f"*Passo 2 de 2:* Agora deposita *R$ {VALOR_MINIMO},00* na SportingBet "
                        "e me manda o *print com o saldo na conta* 💰\n\n"
                        "_Assim que confirmar, te adiciono no grupo de forma vitalícia!_ 🔥"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(e)
            novo_caption = (query.message.caption or "") + "\n\n✅ *Aprovado — aguardando depósito.*"
            salvar_log(user_id, username, "boas-vindas APROVADO")

        elif tipo == "deposito":
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"🎉 *Tudo certo, você foi aprovado!*\n\n"
                        f"Acessa o grupo aqui 👇\n{GRUPO_LINK}\n\n"
                        "Seja bem-vindo e vem leve ein 👀🔥"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(e)
            novo_caption = (query.message.caption or "") + "\n\n✅ *APROVADO — link do grupo enviado!*"
            salvar_log(user_id, username, "depósito APROVADO — link enviado")

    elif acao == "rejeitar":
        tipo_label = "cadastro" if tipo == "boasvindas" else "depósito"
        instrucao = (
            "a tela inicial/boas-vindas da SportingBet esteja visível"
            if tipo == "boasvindas"
            else f"o saldo de R$ {VALOR_MINIMO},00 apareça claramente"
        )
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"⚠️ Não consegui verificar seu print de *{tipo_label}*.\n\n"
                    "Manda novamente com:\n"
                    "• Imagem nítida\n"
                    "• Nome SportingBet visível\n"
                    f"• Certifique-se que {instrucao}\n\n"
                    "Qualquer dúvida é só falar! 🙏"
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(e)
        novo_caption = (query.message.caption or "") + "\n\n❌ *Rejeitado — usuário notificado.*"
        salvar_log(user_id, username, f"{tipo} REJEITADO")
        usuarios[user_id]["etapa"] = "aguarda_boasvindas" if tipo == "boasvindas" else "aguarda_deposito"

    try:
        await query.edit_message_caption(caption=novo_caption, parse_mode="Markdown")
    except Exception:
        pass

# ─── HANDLERS GENÉRICOS ───────────────────────────────────────────────────────
async def foto_generica(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    etapa = usuarios.get(user.id, {}).get("etapa", "")
    if etapa == "aguarda_deposito":
        await receber_print_deposito(update, context)
    elif etapa in ("boasvindas_em_analise", "deposito_em_analise"):
        await update.message.reply_text("⏳ Seu print ainda está em análise, aguarda um pouco!")
    else:
        await update.message.reply_text("Manda /start pra começar 👋")

async def texto_generico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    etapa = usuarios.get(update.effective_user.id, {}).get("etapa", "")
    if etapa in ("boasvindas_em_analise", "deposito_em_analise"):
        await update.message.reply_text("⏳ Seu print está em análise, aguarda!")
    elif etapa == "aguarda_deposito":
        await update.message.reply_text("Me manda o *print do depósito* 📸", parse_mode="Markdown")
    else:
        await update.message.reply_text("Manda /start pra começar 👋")

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Ok! Se mudar de ideia é só mandar /start 😉")
    return ConversationHandler.END

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
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
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(callback_admin, pattern="^(aprovar|rejeitar):"))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, foto_generica))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, texto_generico))

    logger.info("Bot iniciado! 🤖")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
