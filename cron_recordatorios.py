import os
from datetime import datetime, timedelta
import requests
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def enviar_mensaje(chat_id: int, texto: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": texto}
    r = requests.post(url, data=data, timeout=10)
    r.raise_for_status()

def main():
    hoy = datetime.utcnow().date()
    manana = hoy + timedelta(days=1)

    # Vuelos cotizados para hoy o mañana, sin pago
    vuelos = (
        supabase.table("cotizaciones")
        .select("id, user_id, fecha, monto, estado")
        .eq("estado", "Cotizado")
        .gte("fecha", str(hoy))
        .lte("fecha", str(manana))
        .execute()
        .data
    )

    for v in vuelos:
        chat_id = int(v["user_id"])
        v_id = v["id"]
        monto = v.get("monto") or "pendiente"
        fecha = v["fecha"][:10]

        texto = (
            f"🔔 Recordatorio de pago\n\n"
            f"ID de vuelo: {v_id}\n"
            f"Fecha: {fecha}\n"
            f"Monto a pagar: {monto}\n\n"
            "Si ya pagaste, envía tu comprobante con el botón "
            "\"📸 Enviar Pago\" en el menú del bot."
        )
        enviar_mensaje(chat_id, texto)

if __name__ == "__main__":
    main()
