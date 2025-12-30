import os
from datetime import datetime, timedelta
import requests
import json
from collections import Counter
import requests
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from flask import (
    Flask, render_template, request,
    redirect, url_for, flash
)
from supabase import create_client, Client
from telegram import Bot, InputMediaPhoto

# ----------------- CONFIG -----------------

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "cambia_esto")

def verificar_email_google_api(email: str) -> bool:
    """
    Verifica email usando Google Identity Toolkit API.
    Requiere: pip install google-auth google-auth-oauthlib
    """
    try:
        url = "https://identitytoolkit.googleapis.com/v3/relyingparty/verifyEmail"
        payload = {
            "email": email,
            "returnSecureToken": False
        }
        # Necesitas API key de Google Cloud
        api_key = os.getenv("AIzaSyB3MeYONY85TGbK94CX1bZuoQ-iwEHb8j4")
        
        response = requests.post(
            f"{url}?key={api_key}",
            json=payload,
            timeout=5
        )
        
        if response.status_code == 200:
            return "error" not in response.json()
        return False
    except Exception as e:
        app.logger.error(f"Error en verificación API Google: {e}")
        return False
def rango_proximos():
    hoy = datetime.utcnow().date()
    hasta = hoy + timedelta(days=5)
    return hoy, hasta


def enviar_mensaje(chat_id: int, texto: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": texto}
    r = requests.post(url, data=data, timeout=10)
    r.raise_for_status()
    
def enviar_foto(chat_id: int, fileobj, caption: str = ""):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": (fileobj.filename, fileobj.stream, fileobj.mimetype)}
    data = {"chat_id": chat_id, "caption": caption}
    r = requests.post(url, data=data, files=files, timeout=20)
    r.raise_for_status()



# ----------------- MAIL GENERATOR -----------------

@app.route("/mail-generator")
def mail_generator():
    return render_template("mail_generator.html")

@app.route("/accion/generar_email", methods=["POST"])
def generar_email():
    nombre = request.form.get("nombre", "").strip()
    apellido = request.form.get("apellido", "").strip()
    numero = request.form.get("numero", "").strip()
    
    if not nombre or not apellido:
        flash("Nombre y apellido son requeridos.", "error")
        return redirect(url_for("mail_generator"))
    
    # Generar variantes de email
    variantes = []
    base_email = f"{nombre.lower()}.{apellido.lower()}{numero if numero else ''}"
    
    # Crear diferentes combinaciones
    combinaciones = [
        f"{base_email}@gmail.com",
        f"{nombre.lower()}{apellido.lower()}{numero}@gmail.com",
        f"{nombre[0].lower()}{apellido.lower()}{numero}@gmail.com",
        f"{apellido.lower()}{nombre[0].lower()}{numero}@gmail.com",
    ]
    
    for email in combinaciones:
        existe = verificar_email_google_api(email)
        
        # Guardar en Supabase
        try:
            supabase.table("emails_generados").insert({
                "email": email,
                "nombre": nombre,
                "apellido": apellido,
                "numero": numero,
                "existe_en_google": existe
            }).execute()
        except Exception as e:
            app.logger.error(f"Error guardando email {email}: {e}")
        
        variantes.append({
            "email": email,
            "existe": existe,
            "estado": "✅ Existe" if existe else "❌ No existe"
        })
    
    return render_template(
        "mail_generator.html",
        variantes=variantes,
        nombre=nombre,
        apellido=apellido,
        numero=numero
    )

@app.route("/mail-generados")
def mail_generados():
    emails = (
        supabase.table("emails_generados")
        .select("*")
        .order("verificado_en", desc=True)
        .execute()
        .data
    )
    
    existe_count = sum(1 for e in emails if e.get("existe_en_google"))
    no_existe_count = len(emails) - existe_count
    
    return render_template(
        "mail_generados.html",
        emails=emails,
        total=len(emails),
        existe_count=existe_count,
        no_existe_count=no_existe_count
    )

# ----------------- GENERAL / ESTADÍSTICAS -----------------
@app.route("/vuelo/<int:vuelo_id>")
def detalle_vuelo(vuelo_id):
    res = (
        supabase.table("cotizaciones")
        .select("*")
        .eq("id", vuelo_id)
        .single()
        .execute()
    )
    if not res.data:
        flash("Vuelo no encontrado.", "error")
        return redirect(url_for("historial"))

    vuelo = res.data
    return render_template("detalle_vuelo.html", vuelo=vuelo)
@app.route("/accion/borrar_vuelo", methods=["POST"])
def borrar_vuelo():
    v_id = request.form.get("id")
    if not v_id:
        flash("Falta ID de vuelo.", "error")
        return redirect(url_for("historial"))

    # Solo permitir borrar si no está pagado ni con QR
    res = (
        supabase.table("cotizaciones")
        .select("estado")
        .eq("id", v_id)
        .single()
        .execute()
    )
    if not res.data:
        flash("Vuelo no encontrado.", "error")
        return redirect(url_for("historial"))

    if res.data["estado"] in ["Pago Confirmado", "QR Enviados"]:
        flash("No se puede borrar un vuelo ya pagado o con QR.", "error")
        return redirect(url_for("detalle_vuelo", vuelo_id=v_id))

    supabase.table("cotizaciones").delete().eq("id", v_id).execute()
    flash("Vuelo borrado correctamente.", "success")
    return redirect(url_for("historial"))

@app.route("/")
def general():
    hoy = datetime.utcnow().date()
    manana = hoy + timedelta(days=1)

    # obtener todos los usernames y contar distintos
    res_usuarios = (
        supabase.table("cotizaciones")
        .select("username")
        .execute()
        .data
    )
    usernames = [r["username"] for r in res_usuarios if r.get("username")]
    usuarios_unicos = len(set(usernames))

    res_total = (
        supabase.table("cotizaciones")
        .select("monto")
        .in_("estado", ["Pago Confirmado", "QR Enviados"])
        .execute()
        .data
    )
    total_recaudado = sum(float(r["monto"]) for r in res_total if r["monto"])

  # urgentes hoy y mañana
    urgentes = (
        supabase.table("cotizaciones")
        .select("*")
        .gte("fecha", str(hoy))
        .lte("fecha", str(manana))
        .in_("estado", ["Esperando confirmación de pago", "Pago Confirmado"])
        .order("fecha", desc=False)
        .order("created_at", desc=True)
        .execute()
        .data
    )  

    return render_template(
        "general.html",
        usuarios_unicos=usuarios_unicos,
        total_recaudado=total_recaudado,
        urgentes=urgentes,
        hoy=hoy,
    )

# ----------------- POR COTIZAR -----------------

@app.route("/por-cotizar")
def por_cotizar():
    pendientes = (
        supabase.table("cotizaciones")
        .select("*")
        .eq("estado", "Esperando atención")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    return render_template("por_cotizar.html", vuelos=pendientes)


@app.route("/accion/cotizar", methods=["POST"])
def accion_cotizar():
    v_id = request.form.get("id")
    monto_total = request.form.get("monto_total")
    porcentaje = request.form.get("porcentaje")

    if not v_id or not monto_total or not porcentaje:
        flash("Falta ID, monto total o porcentaje.", "error")
        return redirect(url_for("por_cotizar"))

    try:
        monto_total = float(monto_total)
        porcentaje = float(porcentaje)
    except ValueError:
        flash("Monto o porcentaje inválidos.", "error")
        return redirect(url_for("por_cotizar"))

    # monto que se cobrará al usuario
    monto_cobrar = round(monto_total * (porcentaje / 100.0), 2)

    res = (
        supabase.table("cotizaciones")
        .update({"monto": monto_cobrar, "estado": "Cotizado"})
        .eq("id", v_id)
        .execute()
    )  

    if not res.data:
        flash("No se encontró el vuelo.", "error")
        return redirect(url_for("por_cotizar"))

    user_id_raw = res.data[0]["user_id"]
    try:
        user_id = int(user_id_raw)
    except Exception:
        app.logger.error(f"user_id no es entero: {user_id_raw}")
        flash("Cotización guardada, pero user_id inválido en la base.", "error")
        return redirect(url_for("por_cotizar"))

    texto = (
        f"💰 Tu vuelo ID {v_id} ha sido cotizado.\n"
        f"Monto a pagar: {monto_cobrar}\n"
        f"(Equivale al {porcentaje}% del total)\n\n"
        "Cuando tengas tu comprobante usa el botón \"📸 Enviar Pago\" en el bot."
    )

    try:
        enviar_mensaje(user_id, texto)
        flash("Cotización enviada y usuario notificado.", "success")
    except Exception as e:
        app.logger.error(f"Error al enviar cotización a Telegram: {e}")
        flash("Cotización guardada pero no se pudo notificar al usuario.", "error")

    return redirect(url_for("por_cotizar"))



# ----------------- VALIDAR PAGOS -----------------

@app.route("/validar-pagos")
def validar_pagos():
    pendientes = (
        supabase.table("cotizaciones")
        .select("*")
        .eq("estado", "Esperando confirmación de pago")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    return render_template("validar_pagos.html", vuelos=pendientes)


@app.route("/accion/confirmar_pago", methods=["POST"])
def accion_confirmar_pago():
    v_id = request.form.get("id")

    if not v_id:
        flash("Falta ID.", "error")
        return redirect(url_for("validar_pagos"))

    res = (
        supabase.table("cotizaciones")
        .update({"estado": "Pago Confirmado"})
        .eq("id", v_id)
        .execute()
    )

    if not res.data:
        flash("No se encontró el vuelo.", "error")
        return redirect(url_for("validar_pagos"))

    user_id_raw = res.data[0]["user_id"]
    try:
        user_id = int(user_id_raw)
    except Exception:
        app.logger.error(f"user_id no es entero: {user_id_raw}")
        flash("Pago confirmado pero user_id inválido en la base.", "error")
        return redirect(url_for("validar_pagos"))

    texto = (
        f"✅ Tu pago para el vuelo ID {v_id} ha sido confirmado.\n"
        "En breve recibirás tus códigos QR."
    )

    try:
        enviar_mensaje(user_id, texto)
        flash("Pago confirmado y usuario notificado.", "success")
    except Exception as e:
        app.logger.error(f"Error al enviar notificación de pago: {e}")
        flash("Pago confirmado pero no se pudo notificar al usuario.", "error")

    return redirect(url_for("validar_pagos"))

# ----------------- POR ENVIAR QR -----------------

@app.route("/por-enviar-qr")
def por_enviar_qr():
    pendientes = (
        supabase.table("cotizaciones")
        .select("*")
        .eq("estado", "Pago Confirmado")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    return render_template("por_enviar_qr.html", vuelos=pendientes)

# ----------------- POR ENVIAR QR -----------------

@app.route("/accion/enviar_qr", methods=["POST"])
def accion_enviar_qr():
    v_id = request.form.get("id")
    fotos = request.files.getlist("fotos")

    if not v_id:
        flash("Falta ID de vuelo.", "error")
        return redirect(url_for("por_enviar_qr"))

    res = (
        supabase.table("cotizaciones")
        .select("user_id")
        .eq("id", v_id)
        .single()
        .execute()
    )

    if not res.data:
        flash("No se encontró el vuelo.", "error")
        return redirect(url_for("por_enviar_qr"))

    user_id_raw = res.data["user_id"]
    try:
        user_id = int(user_id_raw)
    except Exception:
        app.logger.error(f"user_id no es entero: {user_id_raw}")
        flash("No se pudieron enviar QRs: user_id inválido.", "error")
        return redirect(url_for("por_enviar_qr"))

    if not fotos or fotos[0].filename == "":
        flash("Adjunta al menos una imagen de QR.", "error")
        return redirect(url_for("por_enviar_qr"))

    instrucciones = (
        f"🎫 INSTRUCCIONES ID: {v_id}\n\n"
        "Instrucciones para evitar caídas:\n"
        "- No agregar el pase a la app de la aerolínea.\n"
        "- No revisar el vuelo, solo si se requiere se confirma "
        "2 horas antes del abordaje.\n"
        "- En caso de caída se sacaría un vuelo en el horario siguiente "
        "(ejemplo: salida 3pm, se reacomoda 5–6pm).\n"
        "- Solo deja guardada la foto de tu pase en tu galería para "
        "llegar al aeropuerto y escanear directamente."
    )

    try:
        enviar_mensaje(user_id, instrucciones)

        # mandar cada foto una por una
        for idx, f in enumerate(fotos):
            caption = f"Códigos QR vuelo ID {v_id}" if idx == 0 else ""
            enviar_foto(user_id, f, caption=caption)

        enviar_mensaje(user_id, "🎉 Disfruta tu vuelo.")

        supabase.table("cotizaciones").update(
            {"estado": "QR Enviados"}
        ).eq("id", v_id).execute()

        flash("QRs enviados y estado actualizado a 'QR Enviados'.", "success")
    except Exception as e:
        app.logger.error(f"Error al enviar QRs a Telegram: {e}")
        flash("No se pudieron enviar los QRs al usuario.", "error")

    return redirect(url_for("por_enviar_qr"))

# ----------------- PRÓXIMOS VUELOS -----------------

@app.route("/proximos-vuelos")
def proximos_vuelos():
    hoy, hasta = rango_proximos()
    proximos = (
        supabase.table("cotizaciones")
        .select("*")
        .gte("fecha", str(hoy))
        .lte("fecha", str(hasta))
        .order("fecha", desc=False)
        .execute()
        .data
    )
    return render_template("proximos_vuelos.html", vuelos=proximos)


# ----------------- HISTORIAL -----------------
# ----------------- HISTORIAL GENERAL -----------------

@app.route("/historial")
def historial():
    vuelos = (
        supabase.table("cotizaciones")
        .select("*")
        .order("created_at", desc=True)
        .limit(300)
        .execute()
        .data
    )
    return render_template("historial.html", vuelos=vuelos)

# ----------------- HISTORIAL POR USUARIO -----------------
@app.route("/historial-usuario/<username>")
def historial_usuario(username):
    vuelos = (
        supabase.table("cotizaciones")
        .select("*")
        .eq("username", username)
        .order("created_at", desc=True)
        .execute()
        .data
    )

    total_pagado = sum(
        float(v["monto"]) for v in vuelos
        if v.get("monto") and v.get("estado") in ["Pago Confirmado", "QR Enviados"]
    )

    pagos_confirmados = sum(
        1 for v in vuelos
        if v.get("estado") in ["Pago Confirmado", "QR Enviados"]
    )

    return render_template(
        "historial_usuario.html",
        username=username,
        vuelos=vuelos,
        total_pagado=total_pagado,
        pagos_confirmados=pagos_confirmados,
    )


# ----------------- MAIN -----------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
