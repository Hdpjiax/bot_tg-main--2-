import asyncio
from telethon import TelegramClient
from telethon.errors import UserBannedError, ChatAdminRequiredError, ChannelPrivateError
from telethon.tl.functions.channels import JoinChannelRequest
import time
import re

class SpamTelegram:
    def __init__(self, api_id, api_hash, phone):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.client = TelegramClient('spam_session', api_id, api_hash)
        self.stats = {
            "enviados": 0,
            "fallidos": 0,
            "baneados": 0,
            "sin_permisos": 0
        }
    
    async def conectar(self):
        """Conecta con Telegram"""
        try:
            await self.client.start(phone=self.phone)
            print("✅ Conectado a Telegram")
            return True
        except Exception as e:
            print(f"❌ Error conectando: {e}")
            return False
    
    def extraer_grupo_id(self, enlace):
        """
        Extrae el ID del grupo de diferentes formatos de enlaces
        - https://t.me/groupname → @groupname
        - https://t.me/joinchat/ABC123 → ABC123
        - t.me/+123456789 → +123456789
        """
        try:
            # Formato: https://t.me/joinchat/ABC123
            if 'joinchat' in enlace:
                return enlace.split('joinchat/')[-1]
            
            # Formato: https://t.me/groupname o t.me/+123456789
            if 't.me/' in enlace:
                return enlace.split('t.me/')[-1].rstrip('/')
            
            # Si es solo el nombre
            return enlace.strip()
        except:
            return None
    
    async def unirse_grupo(self, grupo_ref):
        """Intenta unirse al grupo"""
        try:
            if grupo_ref.startswith('+'):
                # Es un hash de invitación
                await self.client(JoinChannelRequest(grupo_ref))
            else:
                # Es un username
                await self.client.get_entity(grupo_ref)
            return True
        except Exception as e:
            print(f"⚠️ No se pudo unir a {grupo_ref}: {e}")
            return False
    
    async def spam_grupo(self, enlace_grupo, mensaje, repeticiones=5, delay=2):
        """
        Envía spam a un grupo
        
        enlace_grupo: Link del grupo (ej: https://t.me/groupname)
        mensaje: Texto a enviar
        repeticiones: Cuántas veces
        delay: Segundos entre mensajes
        """
        grupo_ref = self.extraer_grupo_id(enlace_grupo)
        
        if not grupo_ref:
            print(f"❌ No se pudo extraer ID del grupo: {enlace_grupo}")
            self.stats["fallidos"] += 1
            return
        
        print(f"\n[SPAM] 🚀 Procesando grupo: {grupo_ref}")
        print(f"[SPAM] 📝 Mensaje: {mensaje[:50]}...")
        print(f"[SPAM] 🔁 Repeticiones: {repeticiones}")
        
        try:
            # Intenta unirse al grupo
            await self.unirse_grupo(grupo_ref)
            
            # Obtener entidad
            try:
                entity = await self.client.get_entity(grupo_ref)
            except:
                print(f"⚠️ No se encontró el grupo: {grupo_ref}")
                self.stats["fallidos"] += 1
                return
            
            # Enviar mensajes
            enviados_aqui = 0
            for i in range(repeticiones):
                try:
                    await self.client.send_message(entity, mensaje)
                    enviados_aqui += 1
                    self.stats["enviados"] += 1
                    print(f"[SPAM] ✅ [{i+1}/{repeticiones}] Enviado a {grupo_ref}")
                
                except UserBannedError:
                    print(f"[SPAM] ❌ Fuiste BANEADO del grupo {grupo_ref}")
                    self.stats["baneados"] += 1
                    break
                
                except ChatAdminRequiredError:
                    print(f"[SPAM] ⚠️ Sin permisos en {grupo_ref}")
                    self.stats["sin_permisos"] += 1
                    break
                
                except ChannelPrivateError:
                    print(f"[SPAM] ❌ Grupo privado: {grupo_ref}")
                    self.stats["fallidos"] += 1
                    break
                
                except Exception as e:
                    print(f"[SPAM] ⚠️ Error: {str(e)[:50]}")
                    self.stats["fallidos"] += 1
                    break
                
                # Delay para evitar bloqueo
                if i < repeticiones - 1:
                    await asyncio.sleep(delay)
            
            print(f"[SPAM] ✅ Completado: {enviados_aqui}/{repeticiones} en {grupo_ref}\n")
        
        except Exception as e:
            print(f"[SPAM] ❌ Error general en {grupo_ref}: {e}\n")
            self.stats["fallidos"] += 1
    
    async def spam_multiples_grupos(self, enlaces_grupos, mensaje, repeticiones=5, delay=2, delay_entre_grupos=5):
        """
        Spam en múltiples grupos
        enlaces_grupos: Lista de links
        """
        print(f"\n{'='*60}")
        print(f"[SPAM] 🎯 INICIANDO SPAM EN {len(enlaces_grupos)} GRUPOS")
        print(f"{'='*60}\n")
        
        self.stats = {"enviados": 0, "fallidos": 0, "baneados": 0, "sin_permisos": 0}
        
        for idx, enlace in enumerate(enlaces_grupos, 1):
            print(f"[SPAM] [{idx}/{len(enlaces_grupos)}] Procesando...")
            
            # Límite: máximo 60 mensajes por hora
            if self.stats["enviados"] >= 60:
                print(f"\n[SPAM] ⚠️ LÍMITE DE 60 MENSAJES/HORA ALCANZADO")
                break
            
            await self.spam_grupo(enlace, mensaje, repeticiones, delay)
            
            # Delay entre grupos para evitar bloqueo
            if idx < len(enlaces_grupos):
                print(f"[SPAM] ⏳ Esperando {delay_entre_grupos}s antes del siguiente grupo...\n")
                await asyncio.sleep(delay_entre_grupos)
        
        self._mostrar_estadisticas()
    
    def _mostrar_estadisticas(self):
        """Muestra estadísticas finales"""
        print(f"\n{'='*60}")
        print(f"[SPAM] 📊 ESTADÍSTICAS FINALES")
        print(f"{'='*60}")
        print(f"✅ Enviados: {self.stats['enviados']}")
        print(f"❌ Fallidos: {self.stats['fallidos']}")
        print(f"🚫 Baneados: {self.stats['baneados']}")
        print(f"⛔ Sin permisos: {self.stats['sin_permisos']}")
        print(f"{'='*60}\n")
        
        return self.stats
    
    async def desconectar(self):
        """Desconecta de Telegram"""
        try:
            await self.client.disconnect()
            print("✅ Desconectado de Telegram")
        except:
            pass
    
    async def test_conexion(self):
        """Prueba la conexión"""
        try:
            me = await self.client.get_me()
            print(f"✅ Conectado como: {me.first_name}")
            return True
        except Exception as e:
            print(f"❌ Error: {e}")
            return False
