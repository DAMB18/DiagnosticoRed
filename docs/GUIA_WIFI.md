# Guía: desconexiones Wi-Fi crónicas en Windows

Caso real que motivó esta herramienta: laptop con tarjeta **Intel Dual Band Wireless-AC 8260** con cortes frecuentes, aunque la señal marcaba 91 %.

## Síntomas que reveló el diagnóstico

| Evidencia | Interpretación |
|---|---|
| Router y internet sanos en el momento del escaneo (0 % pérdida) | El fallo es intermitente y local, no del proveedor |
| 21 desconexiones y 12 conexiones fallidas en 7 días | Inestabilidad crónica |
| 7 fallos "Dynamic key exchange did not succeed" | Se asocia al router pero no completa el handshake WPA |
| ~490 eventos "BSS missed beacons" con señal del 91 % | Pierde contacto con el router aunque la señal es fuerte: energía, driver o interferencia |
| Conectado en 2.4 GHz, 144 Mbps | Canal de 40 MHz en la banda más congestionada |
| Reinicios del miniport del driver | El driver se cae y se reinicia solo |

> Una señal alta **no** implica una conexión estable. Los eventos del registro (`Netwtw*` para drivers Intel) dicen más que las barras de señal.

## Ajustes aplicados

1. **Ancho de canal 2.4 GHz → `20 MHz Only`**
   `Win + X` → Administrador de dispositivos → Adaptadores de red → doble clic en la tarjeta → pestaña *Opciones avanzadas* → `Channel Width for 2.4GHz`.
2. **Roaming Aggressiveness → `3. Medium`** (misma pestaña). Con un solo router, un roaming agresivo provoca escaneos y saltos innecesarios.
3. **Ahorro de energía del adaptador inalámbrico → Rendimiento máximo** (también con batería)
   `Win + R` → `powercfg.cpl` → Cambiar la configuración del plan → Cambiar la configuración avanzada → Configuración del adaptador inalámbrico → Modo de ahorro de energía.
4. **Banda 5 GHz.** Tras los cambios anteriores la tarjeta se reconectó a 5 GHz (canal 157, 866 Mbps). Si tu router ofrece 5 GHz, úsala; sufre menos interferencia que 2.4 GHz.
5. **DNS → `1.1.1.1` y `1.0.0.1`** (`Win + R` → `ncpa.cpl` → Propiedades del Wi-Fi → IPv4 → Propiedades). En las pruebas respondieron ~2× más rápido que `8.8.8.8`.
   Cuidado al teclear el alternativo: es `1.0.0.1`, no `1.0.1.0`. La herramienta avisa si un DNS configurado no responde.

## Cómo verificar el efecto

Los contadores de 7 días incluyen el historial *anterior* a los cambios, así que siguen mostrando "fallo". Para medir solo lo posterior:

```bash
python diagnostico_red.py --cli --since "AAAA-MM-DD HH:MM"
```

(usa la hora en que hiciste el último cambio). Usa el equipo con normalidad uno o dos días, incluida la suspensión y el uso con batería, y vuelve a escanear. Pocas o ninguna desconexión iniciada por el driver indica que el problema se resolvió.

## Si persisten las caídas

- Probar por **cable Ethernet**: si con cable todo es estable, el problema es del enlace inalámbrico.
- Cambiar el **canal** del router (en 2.4 GHz solo 1, 6 u 11) y fijar el ancho en 20 MHz.
- Reemplazar la tarjeta (las AC 8260 son de 2015 y Intel ya no las actualiza) o usar un **adaptador USB Wi-Fi 5/6**.
- Si con enlace local sano hay pérdida hacia internet de forma repetida, reunir varios informes y contactar al proveedor.
