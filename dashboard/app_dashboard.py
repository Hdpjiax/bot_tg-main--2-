import os
from datetime import datetime, timedelta
import requests
import json
from collections import Counter
import requests
from urllib.parse import urlencode
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
# ============================================================================
# MAIL GENERATOR - FUNCIONES PARA app_dashboard.py
# ============================================================================

from flask import render_template, request, jsonify
from datetime import datetime
from urllib.parse import quote

# ============================================================================
# MODELO: EmailGenerado (Supabase)
# ============================================================================

class EmailGenerado:
    """Modelo para emails generados en la BD"""
    
    def __init__(self, supabase_client):
        self.db = supabase_client
        self.table = "emails_generados"
    
    def crear(self, email, nombre, apellido, existe=None):
        """Crea un nuevo email en la BD"""
        try:
            data = {
                "email": email,
                "nombre": nombre,
                "apellido": apellido,
                "existe_en_google": existe,
                "verificado_en": datetime.now().isoformat() if existe is not None else None,
                "created_at": datetime.now().isoformat()
            }
            response = self.db.table(self.table).insert(data).execute()
            return response.data[0] if response.data else None
        except Exception as e:
            print(f"Error creando email: {e}")
            return None
    
    def actualizar(self, email, existe):
        """Actualiza el estado de un email"""
        try:
            data = {
                "existe_en_google": existe,
                "verificado_en": datetime.now().isoformat()
            }
            response = self.db.table(self.table).update(data).eq("email", email).execute()
            return response.data[0] if response.data else None
        except Exception as e:
            print(f"Error actualizando email: {e}")
            return None
    
    def obtener_todos(self):
        """Obtiene todos los emails ordenados por fecha"""
        try:
            response = self.db.table(self.table).select("*").order("created_at", desc=True).execute()
            return response.data if response.data else []
        except Exception as e:
            print(f"Error obteniendo emails: {e}")
            return []
    
    def obtener_estadisticas(self):
        """Obtiene estadísticas de emails"""
        try:
            all_emails = self.obtener_todos()
            total = len(all_emails)
            existe_count = len([e for e in all_emails if e.get("existe_en_google") == True])
            no_existe_count = len([e for e in all_emails if e.get("existe_en_google") == False])
            return {
                "total": total,
                "existe_count": existe_count,
                "no_existe_count": no_existe_count
            }
        except Exception as e:
            print(f"Error obteniendo estadísticas: {e}")
            return {"total": 0, "existe_count": 0, "no_existe_count": 0}


# ============================================================================
# GENERADOR DE VARIANTES
# ============================================================================

def generar_variantes(nombre, apellido, numero=""):
    """
    Genera variantes de emails basadas en nombre y apellido
    
    Ejemplos:
    - juan perez → juan.perez, juanperez, jp, j.perez, etc.
    """
    nombre = nombre.lower().strip()
    apellido = apellido.lower().strip()
    numero = numero.strip() if numero else ""
    
    variantes = set()
    
    # Iniciales
    iniciales = nombre[0] + apellido[0]
    
    # Variantes básicas
    variantes.add(f"{nombre}.{apellido}")          # juan.perez
    variantes.add(f"{nombre}{apellido}")           # juanperez
    variantes.add(f"{apellido}.{nombre}")          # perez.juan
    variantes.add(f"{apellido}{nombre}")           # perezjuan
    variantes.add(iniciales)                       # jp
    variantes.add(f"{nombre[0]}.{apellido}")       # j.perez
    variantes.add(f"{apellido}.{nombre[0]}")       # perez.j
    
    # Con número
    if numero:
        variantes.add(f"{nombre}{numero}")         # juan123
        variantes.add(f"{apellido}{numero}")       # perez123
        variantes.add(f"{nombre}.{numero}")        # juan.123
        variantes.add(f"{nombre}{apellido}{numero}") # juanperez123
    
    # Agregar dominio
    variantes_gmail = [f"{v}@gmail.com" for v in variantes]
    
    return sorted(list(variantes_gmail))


# ============================================================================
# VERIFICACIÓN EN GOOGLE
# ============================================================================

def generar_url_verificacion_google(email):
    """
    Genera la URL para verificar un email en Google
    Usa Google Account Recovery sin necesidad de contraseña
    """
    email_encoded = quote(email.split('@')[0])
    url = f"https://accounts.google.com/signin/recovery?email={email_encoded}"
    return url


# ============================================================================
# RUTAS FLASK
# ============================================================================

def register_mail_generator_routes(app, supabase_client):
    """Registra todas las rutas del Mail Generator"""
    
    email_model = EmailGenerado(supabase_client)
    
    # ────────────────────────────────────────────────────────────────────
    # RUTA: GET /mail-generator
    # ────────────────────────────────────────────────────────────────────
    @app.route('/mail-generator', methods=['GET'])
    def mail_generator():
        """Página principal del generador"""
        variantes = request.args.get('variantes')
        nombre = request.args.get('nombre', '')
        apellido = request.args.get('apellido', '')
        
        # Obtener estadísticas
        stats = email_model.obtener_estadisticas()
        
        # Si viene con parámetros, mostrar variantes
        if variantes:
            variantes_list = variantes.split(',')
            variantes_data = [
                {
                    "email": v,
                    "existe": None  # Pendiente de verificar
                }
                for v in variantes_list
            ]
        else:
            variantes_data = None
        
        return render_template(
            'mail_generator.html',
            variantes=variantes_data,
            nombre=nombre,
            apellido=apellido,
            total=stats['total'],
            existe_count=stats['existe_count'],
            no_existe_count=stats['no_existe_count']
        )
    
    # ────────────────────────────────────────────────────────────────────
    # RUTA: POST /generar_email
    # ────────────────────────────────────────────────────────────────────
    @app.route('/generar_email', methods=['POST'])
    def generar_email():
        """Genera variantes de emails"""
        nombre = request.form.get('nombre', '').strip()
        apellido = request.form.get('apellido', '').strip()
        numero = request.form.get('numero', '').strip()
        
        if not nombre or not apellido:
            stats = email_model.obtener_estadisticas()
            return render_template(
                'mail_generator.html',
                variantes=None,
                nombre='',
                apellido='',
                error="Nombre y apellido son requeridos",
                total=stats['total'],
                existe_count=stats['existe_count'],
                no_existe_count=stats['no_existe_count']
            )
        
        # Generar variantes
        variantes = generar_variantes(nombre, apellido, numero)
        
        # Crear registros en BD
        variantes_data = []
        for email in variantes:
            email_model.crear(email, nombre, apellido)
            variantes_data.append({
                "email": email,
                "existe": None  # Pendiente
            })
        
        # Obtener estadísticas actualizadas
        stats = email_model.obtener_estadisticas()
        
        return render_template(
            'mail_generator.html',
            variantes=variantes_data,
            nombre=nombre,
            apellido=apellido,
            total=stats['total'],
            existe_count=stats['existe_count'],
            no_existe_count=stats['no_existe_count']
        )
    
    # ────────────────────────────────────────────────────────────────────
    # RUTA: GET /verificar-email-gmail/{email}
    # ────────────────────────────────────────────────────────────────────
    @app.route('/verificar-email-gmail/<email>', methods=['GET'])
    def verificar_email_gmail(email):
        """Retorna la URL para verificar en Google"""
        url = generar_url_verificacion_google(email)
        return jsonify({"url": url})
    
    # ────────────────────────────────────────────────────────────────────
    # RUTA: POST /guardar-verificacion-email
    # ────────────────────────────────────────────────────────────────────
    @app.route('/guardar-verificacion-email', methods=['POST'])
    def guardar_verificacion_email():
        """Guarda el resultado de la verificación"""
        try:
            data = request.get_json()
            
            email = data.get('email', '').strip()
            existe = data.get('existe')
            nombre = data.get('nombre', '').strip()
            apellido = data.get('apellido', '').strip()
            
            if not email or existe is None:
                return jsonify({"success": False, "error": "Datos incompletos"}), 400
            
            # Actualizar en BD
            email_model.actualizar(email, existe)
            
            return jsonify({"success": True, "message": "Email guardado correctamente"})
        except Exception as e:
            print(f"Error guardando verificación: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    
    # ────────────────────────────────────────────────────────────────────
    # RUTA: GET /mail-generados
    # ────────────────────────────────────────────────────────────────────
    @app.route('/mail-generados', methods=['GET'])
    def mail_generados():
        """Página de historial de emails"""
        emails = email_model.obtener_todos()
        stats = email_model.obtener_estadisticas()
        
        return render_template(
            'mail_generados.html',
            emails=emails,
            total=stats['total'],
            existe_count=stats['existe_count'],
            no_existe_count=stats['no_existe_count']
        )


# ----------------- MAIN -----------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
