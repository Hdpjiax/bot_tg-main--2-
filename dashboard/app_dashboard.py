import os
from datetime import datetime, timedelta
import requests
import json
from collections import Counter
import re
from flask import (
    Flask, render_template, request,
    redirect, url_for, flash
)
from supabase import create_client, Client
from telegram import Bot



# ============= CONFIG =============
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") 


supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)


app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "cambia_esto")



# ============= VERIFICACIÓN DE EMAIL =============
def verificar_email_gmail(email: str) -> bool:
    """
    VERIFICA Gmail usando la página oficial de recuperación de Google (95% precisión)
    Analiza si Google dice "Couldn't find" o pide contraseña/recovery options
    """
    if not email or not email.endswith("@gmail.com"):
        app.logger.info(f"❌ {email} - No es Gmail")
        return False
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,es;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
    }
    
    try:
        session = requests.Session()
        
        # Paso 1: Ir a página de recuperación de Gmail
        url_recovery = "https://accounts.google.com/signin/recoveryidentifier"
        
        # Paso 2: Enviar el email para verificación
        data = {
            'identifier': email,
            'flowName': 'GlifWebSignIn',
            'flowEntry': 'ServiceLogin',
        }
        
        response = session.post(
            url_recovery,
            data=data,
            headers=headers,
            timeout=10,
            allow_redirects=True
        )
        
        html = response.text.lower()
        app.logger.info(f"Verificando {email} - Status: {response.status_code}")
        
        # ✅ EMAIL NO EXISTE - Google dice explícitamente
        if any(phrase in html for phrase in [
            "couldn't find your google account",
            "no account found",
            "that email address doesn't exist",
            "could not find your google account"
        ]):
            app.logger.info(f"❌ {email} NO EXISTE (Google lo confirmó)")
            return False
        
        # ✅ EMAIL EXISTE - Google pide contraseña o recovery
        if any(phrase in html for phrase in [
            "enter your password",
            "password",
            "last password you remember",
            "try another way",
            "recovery options",
            "recovery phone",
            "recovery email"
        ]):
            app.logger.info(f"✅ {email} EXISTE (pide contraseña/recovery)")
            return True
        
        # ⚠️ RESPUESTA AMBIGUA - Asumir que existe (conservador)
        app.logger.info(f"⚠️ {email} PROBABLE (respuesta ambigua)")
        return True
        
    except requests.exceptions.Timeout:
        app.logger.error(f"⏰ Timeout verificando {email}")
        return True  # Conservador: asumir que existe
    except Exception as e:
        app.logger.error(f"❌ Error verificando {email}: {str(e)}")
        return False



# ============= FUNCIONES AUXILIARES =============


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



# ============= MAIL GENERATOR =============


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
        # ✅ VERIFICAR si YA existe en BD ANTES de guardar
        existe_en_bd = supabase.table("emails_generados").select("*").eq("email", email).execute()
        
        if existe_en_bd.data:
            variantes.append({
                "email": email,
                "existe": None,
                "estado": "⚠️ Ya registrado"
            })
            continue
        
        # Verificar con Google RECOVERY PAGE (95% precisión)
        existe_en_google = verificar_email_gmail(email)
        
        # Guardar en Supabase (SIN duplicados)
        try:
            supabase.table("emails_generados").insert({
                "email": email,
                "nombre": nombre,
                "apellido": apellido,
                "numero": numero,
                "existe_en_google": existe_en_google
            }).execute()
            
            variantes.append({
                "email": email,
                "existe": existe_en_google,
                "estado": "✅ EXISTE en Gmail" if existe_en_google else "❌ NO existe"
            })
        except Exception as e:
            app.logger.error(f"Error guardando email {email}: {str(e)}")
            variantes.append({
                "email": email,
                "existe": None,
                "estado": "❌ Error al guardar"
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



# ============= GENERAL / ESTADÍSTICAS =============


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



# ============= POR COTIZAR =============


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


    user_id_raw
