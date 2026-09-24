# DiagnosticoRed

Herramienta de diagnóstico de red para **Windows** que revisa todos los adaptadores de la laptop y resume su estado en un veredicto claro (sano / advertencias / problemas), con la causa probable de cada hallazgo.

Está pensada para responder la pregunta *"¿por qué se me cae el internet?"* separando el problema por capas: **adaptador → enlace local (router) → internet → DNS → Wi-Fi/driver**.

- 100 % **solo lectura**: no cambia ninguna configuración del sistema.
- **Sin dependencias**: solo Python 3.8+ (biblioteca estándar) y las herramientas que ya trae Windows (`PowerShell`, `netsh`, `ping`, `nslookup`).
- Interfaz gráfica (tkinter) y modo consola.
- Informes en español, exportables a `.txt`.

## Qué revisa

| Área | Detalle |
|---|---|
| Adaptadores | Estado, IP, puerta de enlace, DNS, DHCP, métrica, velocidad, driver (proveedor/versión/fecha), errores y descartes de paquetes, propiedades avanzadas |
| Wi-Fi | SSID, señal, banda (2.4/5 GHz), canal, estándar 802.11, velocidades rx/tx, seguridad, redes vecinas por canal (congestión) |
| Conectividad | 20 pings al router, a `1.1.1.1` y a `8.8.8.8` (pérdida, mín/prom/máx, jitter), resolución DNS del sistema, velocidad de cada servidor DNS configurado vs. públicos, prueba HTTP real (`msftconnecttest.com`) |
| Historial | Eventos de WLAN-AutoConfig y del registro del sistema en una ventana de tiempo configurable: desconexiones (separando las iniciadas por el driver de las manuales), fallos de intercambio de claves WPA, "BSS missed beacons", reinicios del driver |

## Instalación y uso

Requiere Windows 10/11 y [Python 3.8+](https://www.python.org/downloads/) en el `PATH`.

```bash
git clone https://github.com/<tu-usuario>/DiagnosticoRed.git
cd DiagnosticoRed
```

**Interfaz gráfica** (doble clic en `DiagnosticoRed.bat`, o):

```bash
python diagnostico_red.py
```

**Modo consola:**

```bash
python diagnostico_red.py --cli
python diagnostico_red.py --cli --since "2026-09-24 15:58"
```

`--since` limita el historial de eventos a partir de esa fecha/hora (por defecto, los últimos 7 días). Es útil para comparar **antes y después** de cambiar un ajuste. En la interfaz hay una lista "Eventos:" con "Últimos 7 días", "Últimas 24 horas", "Última hora", o puedes escribir una fecha `AAAA-MM-DD HH:MM`.

Un escaneo completo tarda unos 25–40 segundos.

## Cómo interpretar el resultado

El diagnóstico sigue una cadena lógica:

1. **Router con pérdida/latencia alta** → el problema está entre la laptop y el router (señal Wi-Fi, interferencia, driver, energía del adaptador). Probar por cable lo confirma.
2. **Router sano pero internet con pérdida** → problema del proveedor (ISP) o del módem.
3. **Ping por IP correcto pero DNS falla** → problema de DNS; cambiar a `1.1.1.1` / `8.8.8.8`.
4. **Todo sano hoy pero muchas desconexiones en el historial** → inestabilidad intermitente; revisar los eventos (driver, key exchange, beacons perdidos).

Reglas principales (`diagnose()` en [diagnostico_red.py](diagnostico_red.py)):

| Condición | Nivel |
|---|---|
| Sin adaptador físico conectado / IP `169.254.x.x` (fallo DHCP) / sin gateway | Fallo |
| Router con ≥5 % de pérdida o >50 ms | Fallo |
| Internet con ≥5 % de pérdida o sin respuesta | Fallo |
| DNS del sistema no resuelve | Fallo |
| Prueba HTTP real falla (portal cautivo, proxy/VPN, corte real) | Fallo |
| Señal Wi-Fi <50 % | Fallo · <70 % aviso |
| Conectado en 2.4 GHz, o >3 redes vecinas en canales solapados | Aviso |
| Windows puede apagar el adaptador para ahorrar energía | Fallo |
| Ancho de canal 2.4 GHz en Auto, roaming agresivo (4–5) | Aviso |
| Desconexiones iniciadas por el driver / fallos de key exchange WPA | Fallo (umbral según la ventana de tiempo) |
| "BSS missed beacons", reinicios del driver | Aviso |

Los umbrales de eventos se ajustan al tamaño de la ventana: en ventanas menores a 72 h basta con 2 eventos para alertar; en 7 días, 5.

## Ejemplo de salida

Ver [docs/ejemplo_informe.txt](docs/ejemplo_informe.txt) (datos de ejemplo, no reales).

## Guía práctica

[docs/GUIA_WIFI.md](docs/GUIA_WIFI.md) recoge los ajustes que resolvieron desconexiones crónicas con una tarjeta Intel AC 8260 (ancho de canal, roaming, ahorro de energía, banda 5 GHz, DNS) y cómo verificar el efecto con la herramienta.

## Estructura del proyecto

```
DiagnosticoRed/
├── diagnostico_red.py     # aplicación (recolección, diagnóstico, informe, GUI/CLI)
├── DiagnosticoRed.bat     # lanzador con doble clic
├── tests/                 # pruebas unitarias del motor de diagnóstico
├── docs/                  # guía y ejemplo de informe
├── LICENSE
└── README.md
```

Organización interna de `diagnostico_red.py`:

- `collect()` — reúne datos (PowerShell → JSON, `netsh`, `ping`, `nslookup`, HTTP, eventos). Las pruebas de red corren en hilos paralelos.
- `diagnose()` — convierte los datos en hallazgos `(nivel, título, recomendación)`.
- `build_report()` — genera el veredicto y las líneas del informe.
- `run_gui()` / bloque `__main__` — interfaz gráfica y CLI.

## Pruebas

```bash
python -m unittest discover -s tests -v
```

Las pruebas usan datos sintéticos; no tocan la red.

## Privacidad y seguridad

- La herramienta **no envía nada** a ningún servidor propio. Las únicas conexiones son los pings a `1.1.1.1` y `8.8.8.8`, consultas DNS y una petición HTTP a `msftconnecttest.com`.
- Los informes guardados incluyen datos de tu red (nombre de la red Wi-Fi, IP local, dirección MAC). **Revísalos antes de compartirlos públicamente.**
- No lee contraseñas de Wi-Fi ni claves.

## Limitaciones conocidas

- Solo Windows. Las claves de `netsh` se leen con reconocimiento de español e inglés; otros idiomas pueden perder algunos campos del Wi-Fi.
- Sin permisos de administrador, algunos ajustes (p. ej. ahorro de energía del adaptador) pueden aparecer como `n/d`.
- Una muestra de 20 pings no es concluyente por sí sola; conviene escanear varias veces.
- Los eventos "BSS missed beacons" son específicos de drivers Intel (`Netwtw*`); otros fabricantes registran eventos distintos.

## Licencia

[MIT](LICENSE)
