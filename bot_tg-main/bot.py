import logging
import os
import threading
import asyncio
import re
from datetime import datetime

from flask import Flask
from supabase import create_client, Client
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto
)
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters
)

# --- 1. SERVIDOR KEEP-ALIVE ---
app_web = Flask('')
app_web.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "bf3145e6595577f099e00638d96e4405b24bb0cd17f6908d34b065943b97dd27"
)

@app_web.route('/')
def home():
    return "Sistema Vuelos Pro - Online 🚀"

def run_server():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host='0.0.0.0', port=port)


# --- 2. CONFIGURACIÓN ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = 7721918273

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SOPORTE_USER = "@TuUsuarioSoporte"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
logging.basicConfig(level=logging.INFO)


# --- 3. TECLADOS ---

def get_user_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📝 Datos de vuelo"), KeyboardButton("📸 Enviar Pago")],
            [KeyboardButton("🆘 Soporte")],
        ],
        resize_keyboard=True,
    )


# --- 4. EXTRAER FECHA DEL TEXTO ---

DATE_PATTERN = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")

def extraer_fecha(texto: str):
    m = DATE_PATTERN.search(texto)
    if not m:
        return None
    d, mth, y = m.groups()
    try:
        dt = datetime(int(y), int(mth), int(d))
        return dt.date().isoformat()
    except ValueError:
        return None


# --- 5. HANDLERS USUARIO ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ Bienvenido al Sistema de Vuelos\nUsa el menú para iniciar.",
        reply_markup=get_user_keyboard(),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    texto = update.message.text
    udata = context.user_data

    # El admin no usa el bot para gestionar, solo el dashboard
    if uid == ADMIN_CHAT_ID:
        await update.message.reply_text("El panel de administración está en la web.")
        return

    if texto == "📝 Datos de vuelo":
        udata.clear()
        udata["estado"] = "usr_esperando_datos"
        await update.message.reply_text(
            "Escribe el Origen, Destino y Fecha de tu vuelo.\n"
            "Ejemplo: CDMX a Cancún el 25-12-2025."
        )

    elif texto == "📸 Enviar Pago":
        udata.clear()
        udata["estado"] = "usr_esperando_id_pago"
        await update.message.reply_text(
            "Escribe el ID del vuelo que vas a pagar."
        )

    elif texto == "🆘 Soporte":
        btn = InlineKeyboardMarkup(
            [[InlineKeyboardButton(
                "Contactar Soporte 💬",
                url=f"https://t.me/{SOPORTE_USER.replace('@','')}"
            )]]
        )
        await update.message.reply_text(
            "Haz clic abajo para hablar con un agente:",
            reply_markup=btn,
        )

    # Usuario manda descripción del vuelo
    elif udata.get("estado") == "usr_esperando_datos":
        udata["tmp_datos"] = texto
        fecha = extraer_fecha(texto)
        udata["tmp_fecha"] = fecha

        if fecha:
            msg_fecha = f"✅ Fecha detectada: {fecha}"
        else:
            msg_fecha = (
                "⚠️ No se detectó una fecha válida. "
                "Escribe la fecha como 25-12-2025."
            )

        udata["estado"] = "usr_esperando_foto_vuelo"
        await update.message.reply_text(
            f"{msg_fecha}\nAhora envía una imagen de referencia del vuelo."
        )

    # Usuario indica ID de vuelo a pagar
    elif udata.get("estado") == "usr_esperando_id_pago":
        v_id = texto.strip()
        res = (
            supabase.table("cotizaciones")
            .select("monto, estado")
            .eq("id", v_id)
            .single()
            .execute()
        )

        if not res.data:
            await update.message.reply_text("❌ ID no encontrado. Verifica tu ID.")
            return

        monto = res.data.get("monto")
        if not monto:
            await update.message.reply_text(
                "⚠️ Ese vuelo aún no tiene monto. Espera a que sea cotizado."
            )
            return

        udata["pago_vuelo_id"] = v_id
        udata["estado"] = "usr_esperando_comprobante"

        texto_msj = (
            f"💳 ID de vuelo: {v_id}\n"
            f"💰 Monto a pagar: {monto}\n\n"
            "🏦 Datos de Pago\n"
            "Banco: BBVA\n"
            "CLABE: 012180015886058959\n"
            "Titular: Antonio Garcia\n\n"
            "Ahora envía la captura del pago como foto."
        )
        await update.message.reply_text(texto_msj)

    else:
        await update.message.reply_text(
            "Usa el menú para continuar.",
            reply_markup=get_user_keyboard(),
        )


# --- 6. FOTOS: NUEVA COTIZACIÓN y COMPROBANTE ---

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid == ADMIN_CHAT_ID:
        return  # admin no gestiona desde el bot

    udata = context.user_data
    if not update.message.photo:
        return

    fid = update.message.photo[-1].file_id

    # 1) Foto de referencia de la cotización
    if udata.get("estado") == "usr_esperando_foto_vuelo":
        fecha = udata.get("tmp_fecha")
        res = (
            supabase.table("cotizaciones")
            .insert(
                {
                    "user_id": str(uid),
                    "username": update.effective_user.username or "SinUser",
                    "pedido_completo": udata.get("tmp_datos"),
                    "estado": "Esperando atención",
                    "monto": None,
                    "fecha": fecha,
                }
            )
            .execute()
        )

        v_id = res.data[0]["id"]

        await update.message.reply_text(
            f"✅ Cotización recibida.\n"
            f"ID de vuelo: {v_id}\n"
            "Un agente revisará tu solicitud y te enviará el monto a pagar."
        )

        # Aviso al admin (solo informativo)
        await context.bot.send_photo(
            ADMIN_CHAT_ID,
            fid,
            caption=(
                "🔔 NUEVA SOLICITUD DE COTIZACIÓN\n"
                f"ID: {v_id}\n"
                f"User: @{update.effective_user.username}\n"
                f"Info: {udata.get('tmp_datos')}"
            ),
        )

        udata.clear()

    # 2) Comprobante de pago (NO crea registros nuevos)
    elif udata.get("estado") == "usr_esperando_comprobante":
        v_id = udata.get("pago_vuelo_id")

        supabase.table("cotizaciones").update(
            {"estado": "Esperando confirmación de pago"}
        ).eq("id", v_id).execute()

        await update.message.reply_text(
            "✅ Comprobante enviado. Tu pago está en revisión."
        )

        # Botón automático para confirmar pago desde el propio Telegram (admin)
        btn_confirmar = InlineKeyboardMarkup(
            [[InlineKeyboardButton(
                f"Confirmar Pago ID {v_id} ✅",
                callback_data=f"conf_pago_{v_id}",
            )]]
        )

        await context.bot.send_photo(
            ADMIN_CHAT_ID,
            fid,
            caption=(
                "💰 COMPROBANTE DE PAGO RECIBIDO\n"
                f"ID Vuelo: `{v_id}`\n"
                f"User: @{update.effective_user.username}"
            ),
            reply_markup=btn_confirmar,
            parse_mode="Markdown",
        )

        udata.clear()


# --- 7. CALLBACK SOLO PARA BOTÓN DE TELEGRAM ---

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_CHAT_ID:
        return

    if query.data.startswith("conf_pago_"):
        v_id = query.data.split("_")[2]

        res = (
            supabase.table("cotizaciones")
            .update({"estado": "Pago Confirmado"})
            .eq("id", v_id)
            .execute()
        )

        if not res.data:
            await query.message.reply_text("No se encontró el vuelo.")
            return

        user_id = res.data[0]["user_id"]

        await context.bot.send_message(
            user_id,
            f"✅ Tu pago para el vuelo ID {v_id} ha sido confirmado.\n"
            "Espera la llegada de tus códigos QR."
        )

        await query.edit_message_caption(
            caption=f"✅ PAGO CONFIRMADO\nID Vuelo: {v_id}"
        )


# --- 8. ARRANQUE ---

if __name__ == "__main__":
    threading.Thread(target=run_server).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()


