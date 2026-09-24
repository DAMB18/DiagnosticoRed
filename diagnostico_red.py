"""DiagnosticoRed - revisa todos los adaptadores de red de la laptop y resume su estado.

Solo LECTURA: no cambia ninguna configuracion. Solo Windows.
Uso:  python diagnostico_red.py          (interfaz grafica)
      python diagnostico_red.py --cli    (informe en consola)
"""
import json
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta

NOWIN = 0x08000000  # CREATE_NO_WINDOW


# ----------------------------------------------------------------- utilidades
def run(cmd, timeout=60):
    """Ejecuta un comando (lista) y devuelve stdout como texto."""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=NOWIN)
        return r.stdout.decode("oem", errors="replace")
    except Exception:
        return ""


def ps_json(script, timeout=90):
    """Ejecuta PowerShell y parsea su salida JSON (lista o dict) -> lista."""
    full = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            "$ErrorActionPreference='SilentlyContinue';" + script)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", full],
            capture_output=True, timeout=timeout, creationflags=NOWIN)
        txt = r.stdout.decode("utf-8", errors="replace").strip()
        if not txt:
            return []
        data = json.loads(txt)
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


def as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


# ----------------------------------------------------------------- recoleccion
PS_ADAPTERS = r"""
$out = foreach ($a in Get-NetAdapter -IncludeHidden:$false) {
  $cfg = Get-NetIPConfiguration -InterfaceIndex $a.ifIndex
  $ip4 = Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4
  $dns = Get-DnsClientServerAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4
  $ipif = Get-NetIPInterface -InterfaceIndex $a.ifIndex -AddressFamily IPv4
  $pm = Get-NetAdapterPowerManagement -Name $a.Name
  $st = Get-NetAdapterStatistics -Name $a.Name
  $adv = Get-NetAdapterAdvancedProperty -Name $a.Name
  [pscustomobject]@{
    Name=$a.Name; Desc=$a.InterfaceDescription; Status=[string]$a.Status
    Media=[string]$a.PhysicalMediaType; Speed=$a.LinkSpeed; Mac=$a.MacAddress
    Virtual=[bool]$a.Virtual; Hardware=[bool]$a.HardwareInterface
    Driver=$a.DriverVersionString; DriverDate=[string]$a.DriverDate
    DriverProvider=$a.DriverProvider
    IPv4=@($ip4 | ForEach-Object { $_.IPAddress })
    Gateway=@($cfg.IPv4DefaultGateway | ForEach-Object { $_.NextHop })
    Dns=@($dns.ServerAddresses)
    Adv=@($adv | ForEach-Object { $_.DisplayName + '=' + $_.DisplayValue })
    Dhcp=[string]$ipif.Dhcp; Metric=$ipif.InterfaceMetric
    PowerSave=[string]$pm.AllowComputerToTurnOffDevice
    RxErr=$st.ReceivedPacketErrors; TxErr=$st.OutboundPacketErrors
    RxDisc=$st.ReceivedDiscardedPackets; TxDisc=$st.OutboundDiscardedPackets
  }
}
$out | ConvertTo-Json -Depth 4 -Compress
"""

PS_EVENTS = r"""
$since = [datetime]'__SINCE__'
$w = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-WLAN-AutoConfig/Operational';StartTime=$since;Id=8001,8002,8003,11006,11010}
$w2 = Get-WinEvent -FilterHashtable @{LogName='System';StartTime=$since;Level=1,2,3} |
      Where-Object { $_.ProviderName -match 'Netwtw|e1[a-z]express|NDIS|Tcpip|Dhcp|netwlan|Netwlv|rtwlane|Netwbw|Netwew' }
$rows = @()
$rows += $w  | ForEach-Object { [pscustomobject]@{Log='WLAN';Time=$_.TimeCreated.ToString('s');Id=$_.Id;Prov=$_.ProviderName;Msg=((($_.Message -split "`n")[0]) + ' ' + (($_.Message -split "`n" | Select-String 'Reason') -join ' ')).Trim()} }
$rows += $w2 | ForEach-Object { [pscustomobject]@{Log='System';Time=$_.TimeCreated.ToString('s');Id=$_.Id;Prov=$_.ProviderName;Msg=(($_.Message -split "`n")[0]).Trim()} }
$rows | ConvertTo-Json -Depth 3 -Compress
"""


def parse_netsh_kv(text):
    """Devuelve lista de (clave, valor) de la salida de netsh (multi-idioma)."""
    pairs = []
    for line in text.splitlines():
        if " : " in line:
            k, v = line.split(" : ", 1)
            pairs.append((k.strip(), v.strip()))
    return pairs


def wifi_info():
    """Info de la conexion Wi-Fi actual y de las redes vecinas (canales)."""
    txt = run(["netsh", "wlan", "show", "interfaces"])
    info = {}
    for k, v in parse_netsh_kv(txt):
        kl = k.lower()
        if kl in ("ssid",):
            info["ssid"] = v
        elif kl == "bssid":
            info["bssid"] = v
        elif "%" in v and re.fullmatch(r"\d+%", v):
            info["signal"] = int(v.rstrip("%"))
        elif kl in ("canal", "channel"):
            try:
                info["channel"] = int(v)
            except ValueError:
                pass
        elif "802.11" in v and ("radio" in kl or "tipo" in kl or "type" in kl):
            info["radio"] = v
        elif "mbps" in kl and ("recep" in kl or "receive" in kl):
            info["rx"] = v
        elif "mbps" in kl and ("transm" in kl):
            info["tx"] = v
        elif kl in ("estado", "state"):
            info["state"] = v
        elif kl in ("autenticación", "autenticacion", "authentication"):
            info["auth"] = v
    if "channel" in info:
        info["band"] = "2.4 GHz" if info["channel"] <= 14 else "5 GHz"

    # redes vecinas para medir congestion de canal
    neighbors = []
    txt = run(["netsh", "wlan", "show", "networks", "mode=bssid"], timeout=30)
    cur_ssid = None
    for k, v in parse_netsh_kv(txt):
        if re.match(r"(?i)^ssid \d+", k):
            cur_ssid = v
        elif k.lower() in ("canal", "channel"):
            try:
                neighbors.append((cur_ssid, int(v)))
            except ValueError:
                pass
    info["neighbors"] = neighbors
    return info


def ping(host, count=20, timeout_ms=1500):
    """Ping n veces. Devuelve dict con perdida, min/avg/max/jitter (ms)."""
    txt = run(["ping", "-n", str(count), "-w", str(timeout_ms), host],
              timeout=count * 3 + 10)
    times = []
    for line in txt.splitlines():
        if "TTL" in line.upper():
            m = re.search(r"[=<]\s*(\d+)\s*ms", line, re.I)
            if m:
                times.append(int(m.group(1)))
    got = len(times)
    res = {"host": host, "sent": count, "recv": got,
           "loss": round(100 * (count - got) / count, 1)}
    if times:
        res.update(min=min(times), max=max(times),
                   avg=round(statistics.mean(times), 1),
                   jitter=round(statistics.pstdev(times), 1))
    return res


def dns_test(name="www.google.com"):
    t0 = time.time()
    try:
        socket.getaddrinfo(name, 80)
        return {"ok": True, "ms": round((time.time() - t0) * 1000)}
    except Exception as e:
        return {"ok": False, "ms": round((time.time() - t0) * 1000), "err": str(e)}


def dns_server_test(server, name="www.google.com"):
    """Consulta DNS via nslookup contra un servidor concreto; devuelve ms o None."""
    t0 = time.time()
    txt = run(["nslookup", name, server], timeout=8)
    ms = round((time.time() - t0) * 1000)
    ok = bool(re.search(r"(?is)address.*\d+\.\d+\.\d+\.\d+.*address.*\d+\.\d+\.\d+\.\d+", txt)) \
        or txt.lower().count("address") >= 2
    return ms if ok else None


def http_test():
    t0 = time.time()
    try:
        with urllib.request.urlopen("http://www.msftconnecttest.com/connecttest.txt",
                                    timeout=8) as r:
            body = r.read().decode(errors="ignore")
        return {"ok": "Microsoft Connect Test" in body,
                "ms": round((time.time() - t0) * 1000)}
    except Exception as e:
        return {"ok": False, "err": str(e)}


def collect(progress=lambda m: None, since=None):
    """since: datetime desde el que se leen los eventos (por defecto, 7 dias)."""
    since = since or datetime.now() - timedelta(days=7)
    d = {"when": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "since": since}
    d["window"] = "desde " + since.strftime("%Y-%m-%d %H:%M")
    progress("Leyendo adaptadores...")
    d["adapters"] = ps_json(PS_ADAPTERS)
    for a in d["adapters"]:
        for key in ("IPv4", "Gateway", "Dns"):
            a[key] = [x for x in as_list(a.get(key)) if x]
    progress("Leyendo Wi-Fi y redes vecinas...")
    d["wifi"] = wifi_info()

    gws = [g for a in d["adapters"] if a["Status"] == "Up" and not a["Virtual"]
           for g in a["Gateway"]]
    d["gateway"] = gws[0] if gws else None

    results = {}

    def job(key, fn):
        results[key] = fn()

    jobs = []
    if d["gateway"]:
        jobs.append(("ping_gw", lambda: ping(d["gateway"])))
    jobs += [("ping_cf", lambda: ping("1.1.1.1")),
             ("ping_g", lambda: ping("8.8.8.8")),
             ("dns", dns_test),
             ("http", http_test)]
    progress("Probando gateway, internet y DNS (~25 s)...")
    ths = [threading.Thread(target=job, args=j) for j in jobs]
    [t.start() for t in ths]
    [t.join() for t in ths]
    d["tests"] = results

    # DNS configurados vs publicos
    dns_cfg = []
    for a in d["adapters"]:
        if a["Status"] == "Up" and not a["Virtual"]:
            dns_cfg += a["Dns"]
    d["dns_speed"] = {}
    progress("Comparando servidores DNS...")
    for s in dict.fromkeys(dns_cfg + ["1.1.1.1", "8.8.8.8"]):
        d["dns_speed"][s] = dns_server_test(s)

    progress("Leyendo registro de eventos...")
    d["events"] = ps_json(PS_EVENTS.replace("__SINCE__", since.strftime("%Y-%m-%dT%H:%M:%S")), timeout=120)
    return d


# ----------------------------------------------------------------- diagnostico
OK, WARN, BAD, INFO = "ok", "warn", "bad", "info"


def diagnose(d):
    """Devuelve lista de (nivel, titulo, detalle/recomendacion)."""
    f = []
    ads = d["adapters"]
    real_up = [a for a in ads if a["Status"] == "Up" and not a["Virtual"]]
    t = d["tests"]
    gw, cf, g8 = t.get("ping_gw"), t.get("ping_cf"), t.get("ping_g")
    wifi = d["wifi"]

    if not real_up:
        f.append((BAD, "Ningun adaptador fisico conectado",
                  "Activa el Wi-Fi o conecta el cable."))
        return f

    for a in real_up:
        if any(ip.startswith("169.254.") for ip in a["IPv4"]):
            f.append((BAD, f"{a['Name']}: IP 169.254.x.x (APIPA)",
                      "No obtuvo IP del router (fallo DHCP). Reinicia el router / renueva IP."))
        if not a["Gateway"]:
            f.append((BAD, f"{a['Name']}: sin puerta de enlace",
                      "Sin gateway no hay salida a internet."))

    # cadena de diagnostico: local -> WAN -> DNS
    if gw:
        if gw["loss"] >= 5 or gw.get("avg", 0) > 50:
            f.append((BAD, f"Enlace LOCAL degradado (gateway {gw['host']}: "
                           f"{gw['loss']}% perdida, {gw.get('avg', '-')} ms)",
                      "El problema esta entre tu laptop y el router: senal Wi-Fi, interferencia, "
                      "driver o energia del adaptador. Prueba por cable para confirmarlo."))
        elif gw.get("jitter", 0) > 20:
            f.append((WARN, f"Latencia inestable al router (jitter {gw['jitter']} ms)",
                      "Suele indicar interferencia de Wi-Fi."))
        else:
            f.append((OK, f"Enlace local sano (gateway {gw['host']}: "
                          f"{gw['loss']}% perdida, {gw.get('avg', '-')} ms)", ""))
    inet = [x for x in (cf, g8) if x]
    if inet:
        worst = max(x["loss"] for x in inet)
        avg = statistics.mean(x["avg"] for x in inet if "avg" in x) if any("avg" in x for x in inet) else None
        if worst >= 100:
            f.append((BAD, "Sin salida a internet por IP",
                      "Si el gateway responde, el fallo esta en el router/modem o el proveedor (ISP)."))
        elif worst >= 5:
            local_ok = gw and gw["loss"] < 2
            f.append((BAD, f"Perdida de paquetes hacia internet ({worst}%)",
                      "Con enlace local sano => problema del ISP/modem. Con enlace local malo => Wi-Fi."
                      if local_ok is not None else "Revisa Wi-Fi/ISP."))
        elif avg and avg > 100:
            f.append((WARN, f"Latencia alta a internet ({avg:.0f} ms)", "Posible congestion o ISP lento."))
        else:
            f.append((OK, "Salida a internet por IP correcta"
                      + (f" ({avg:.0f} ms)" if avg else ""), ""))
        if any(x.get("jitter", 0) > 30 for x in inet):
            f.append((WARN, "Jitter alto hacia internet",
                      "Causa cortes en videollamadas/juegos; revisa senal o carga de la red."))

    dns = t.get("dns", {})
    if not dns.get("ok"):
        f.append((BAD, "Falla la resolucion DNS",
                  "Si el ping por IP funciona pero no hay paginas, cambia el DNS (1.1.1.1 / 8.8.8.8)."))
    elif dns["ms"] > 300:
        f.append((WARN, f"DNS lento ({dns['ms']} ms)", "Considera DNS 1.1.1.1 u 8.8.8.8."))
    else:
        f.append((OK, f"Resolucion DNS correcta ({dns['ms']} ms)", ""))

    ds = d.get("dns_speed", {})
    cfg = [s for s, v in ds.items() if s not in ("1.1.1.1", "8.8.8.8")]
    for s in cfg:
        if ds[s] is None:
            f.append((WARN, f"El servidor DNS configurado {s} no responde",
                      "Cambia a un DNS publico fiable."))

    h = t.get("http", {})
    if h.get("ok"):
        f.append((OK, f"Prueba HTTP real OK ({h['ms']} ms)", ""))
    else:
        f.append((BAD, "Prueba HTTP real fallida",
                  "Puede haber portal cautivo, proxy/VPN o corte real de internet."))

    # Wi-Fi
    for a in real_up:
        if a["Media"] and "802.11" in a["Media"] or "wi-fi" in a["Name"].lower() or "wireless" in a["Desc"].lower():
            sig = wifi.get("signal")
            if sig is not None:
                lvl = OK if sig >= 70 else WARN if sig >= 50 else BAD
                f.append((lvl, f"Senal Wi-Fi {sig}% ({wifi.get('band', '?')}, canal {wifi.get('channel', '?')}, "
                               f"{wifi.get('radio', '')})",
                          "" if lvl == OK else "Acercate al router o usa banda 5 GHz / repetidor."))
            if wifi.get("band") == "2.4 GHz":
                f.append((WARN, "Conectado en 2.4 GHz",
                          "Es la banda mas congestionada; si tu router tiene 5 GHz usala."))
            ch = wifi.get("channel")
            if ch:
                same = [s for s, c in wifi["neighbors"] if abs(c - ch) <= (4 if ch <= 14 else 0)]
                if len(same) > 3:
                    f.append((WARN, f"Congestion: {len(same)} redes vecinas en canales solapados",
                              "Cambia el canal del router (en 2.4 GHz usa 1, 6 u 11)."))
            if a["PowerSave"].lower() in ("enabled", "true", "1"):
                f.append((BAD, f"'{a['Name']}': Windows puede apagar el adaptador para ahorrar energia",
                          "Causa comun de desconexiones. Administrador de dispositivos > adaptador > "
                          "Administracion de energia > desmarcar 'Permitir que el equipo apague este dispositivo'."))
            adv = dict(x.split("=", 1) for x in as_list(a.get("Adv")) if "=" in x)
            if adv.get("Channel Width for 2.4GHz", "").lower() in ("auto",) and wifi.get("band") == "2.4 GHz":
                f.append((WARN, "Ancho de canal 2.4 GHz en Auto (puede usar 40 MHz)",
                          "Fijalo en '20 MHz Only' en Opciones avanzadas del adaptador."))
            if adv.get("Roaming Aggressiveness", "").startswith(("4", "5")):
                f.append((WARN, f"Roaming Aggressiveness alto ({adv['Roaming Aggressiveness']})",
                          "Con un solo router provoca escaneos/saltos innecesarios; usa '3. Medium'."))

    for a in real_up:
        errs = sum(int(a.get(k) or 0) for k in ("RxErr", "TxErr", "RxDisc", "TxDisc"))
        if errs > 100:
            f.append((WARN, f"'{a['Name']}': {errs} paquetes con errores/descartados",
                      "Indica problemas de senal, cable o driver."))

    # eventos (umbrales segun el tamano de la ventana)
    ev = as_list(d.get("events"))
    win = d.get("window", "en 7 dias")
    hours = (datetime.now() - d["since"]).total_seconds() / 3600 if d.get("since") else 168
    hard = 5 if hours >= 72 else 2
    wl = lambda i: [e for e in ev if e.get("Log") == "WLAN" and e.get("Id") == i]
    disc, fail, secfail = wl(8003), wl(8002), wl(11006)
    by_driver = [e for e in disc if "by the driver" in (e.get("Msg") or "").lower()]
    by_user = len(disc) - len(by_driver)
    if len(by_driver) >= hard:
        f.append((BAD, f"{len(by_driver)} desconexiones iniciadas por el driver {win} "
                       f"(+{by_user} manuales/otras, {len(fail)} intentos fallidos)",
                  "El driver se cae solo: revisa energia del adaptador, senal y router."))
    elif disc:
        f.append((INFO if len(by_driver) == 0 else WARN,
                  f"{len(disc)} desconexion(es) Wi-Fi {win} ({len(by_driver)} del driver, "
                  f"{by_user} manuales/otras)", ""))
    else:
        f.append((OK, f"Sin desconexiones Wi-Fi registradas {win}", ""))
    keyx = [e for e in fail if "key exchange" in (e.get("Msg") or "").lower()]
    if len(keyx) >= max(2, hard - 3):
        f.append((BAD, f"{len(keyx)} fallos de intercambio de claves WPA {win}",
                  "Se asocia pero no completa el handshake: tipico de interferencia en 2.4 GHz, "
                  "driver o router. Prueba 5 GHz y 'Olvidar red' + reconectar."))
    elif len(secfail) >= hard:
        f.append((WARN, f"{len(secfail)} fallos de seguridad Wi-Fi {win}", ""))
    beacons = [e for e in ev if e.get("Id") == 6000 and "beacon" in (e.get("Msg") or "").lower()]
    if len(beacons) >= max(3, hard):
        f.append((WARN, f"{len(beacons)} eventos 'BSS missed beacons' {win}",
                  "El adaptador pierde el contacto con el router momentaneamente."))
    resets = [e for e in ev if e.get("Id") == 8000]
    if len(resets) >= 2:
        f.append((WARN, f"El driver se reinicio (miniport halt) {len(resets)} veces {win}", ""))
    return f


# ----------------------------------------------------------------- informe
ICON = {OK: "[ OK ]", WARN: "[AVISO]", BAD: "[FALLO]", INFO: "[info]"}


def build_report(d):
    """Devuelve (veredicto_nivel, veredicto_texto, lista_de_lineas(tag,texto))."""
    findings = diagnose(d)
    lv = [x[0] for x in findings]
    verdict = BAD if BAD in lv else WARN if WARN in lv else OK
    vtext = {BAD: "PROBLEMAS DETECTADOS", WARN: "FUNCIONA, CON ADVERTENCIAS",
             OK: "RED SANA"}[verdict]
    L = [("h", f"Informe de red - {d['when']}"), ("", "")]

    L.append(("h", "DIAGNOSTICO"))
    for lvl, title, det in findings:
        L.append((lvl, f"{ICON[lvl]} {title}"))
        if det:
            L.append(("dim", f"         -> {det}"))
    L.append(("", ""))

    L.append(("h", "ADAPTADORES"))
    for a in sorted(d["adapters"], key=lambda a: (a["Status"] != "Up", a["Virtual"])):
        up = a["Status"] == "Up"
        tag = ("ok" if up else "dim")
        kind = "virtual" if a["Virtual"] else "fisico"
        L.append((tag, f"* {a['Name']}  [{a['Status']}, {kind}]  {a['Desc']}"))
        if up:
            L.append(("dim", f"    IP: {', '.join(a['IPv4']) or '-'}   Gateway: {', '.join(a['Gateway']) or '-'}   "
                             f"DNS: {', '.join(a['Dns']) or '-'}"))
            L.append(("dim", f"    Velocidad: {a['Speed']}   DHCP: {a['Dhcp']}   Metrica: {a['Metric']}   "
                             f"MAC: {a['Mac']}"))
        L.append(("dim", f"    Driver: {a['DriverProvider']} {a['Driver']} ({(a['DriverDate'] or '')[:10]})   "
                         f"Ahorro de energia: {a['PowerSave'] or 'n/d'}"))
    L.append(("", ""))

    w = d["wifi"]
    if w.get("ssid"):
        L.append(("h", "WI-FI ACTUAL"))
        L.append(("", f"  Red: {w['ssid']}   Senal: {w.get('signal', '?')}%   {w.get('band', '')} "
                      f"canal {w.get('channel', '?')}   {w.get('radio', '')}"))
        L.append(("", f"  Velocidad rx/tx: {w.get('rx', '?')} / {w.get('tx', '?')} Mbps   "
                      f"Seguridad: {w.get('auth', '?')}"))
        if w["neighbors"]:
            from collections import Counter
            cnt = Counter(c for _, c in w["neighbors"])
            L.append(("dim", "  Redes vecinas por canal: " +
                      ", ".join(f"{c}:{n}" for c, n in sorted(cnt.items()))))
        L.append(("", ""))

    L.append(("h", "PRUEBAS DE CONECTIVIDAD"))
    for key, label in (("ping_gw", "Router"), ("ping_cf", "Cloudflare 1.1.1.1"), ("ping_g", "Google 8.8.8.8")):
        p = d["tests"].get(key)
        if p:
            avg = f"{p['avg']} ms (min {p['min']}, max {p['max']}, jitter {p['jitter']})" if "avg" in p else "sin respuesta"
            L.append(("", f"  {label:<20} {p['host']:<15} perdida {p['loss']}%   {avg}"))
    L.append(("", "  DNS del sistema: " + (f"OK {d['tests']['dns']['ms']} ms" if d["tests"]["dns"]["ok"] else "FALLA")))
    for s, v in d["dns_speed"].items():
        L.append(("dim", f"    servidor {s}: " + (f"{v} ms" if v is not None else "no responde")))
    L.append(("", ""))

    ev = as_list(d.get("events"))
    if ev:
        L.append(("h", f"EVENTOS RECIENTES ({d['window']})"))
        for e in sorted(ev, key=lambda e: e["Time"], reverse=True)[:15]:
            msg = re.sub(r"\s+", " ", e["Msg"] or "")[:110]
            L.append(("dim", f"  {e['Time'].replace('T', ' ')}  {e['Log']}/{e['Id']}  {e['Prov']}: {msg}"))
    return verdict, vtext, L


# ----------------------------------------------------------------- GUI
WINDOWS = {"Ultimos 7 dias": timedelta(days=7), "Ultimas 24 horas": timedelta(hours=24),
           "Ultima hora": timedelta(hours=1)}


def parse_since(text):
    """Acepta una ventana predefinida o una fecha 'AAAA-MM-DD HH:MM'. None si es invalida."""
    text = (text or "").strip()
    if text in WINDOWS:
        return datetime.now() - WINDOWS[text]
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, scrolledtext, ttk

    root = tk.Tk()
    root.title("Diagnostico de Red")
    root.geometry("980x720")
    colors = {OK: "#1e8e3e", WARN: "#b06000", BAD: "#c5221f", INFO: "#1a73e8"}

    banner = tk.Label(root, text="Pulsa 'Escanear'", font=("Segoe UI", 16, "bold"),
                      bg="#5f6368", fg="white", pady=10)
    banner.pack(fill="x")
    bar = tk.Frame(root)
    bar.pack(fill="x", padx=8, pady=6)
    status = tk.Label(bar, text="", anchor="w")
    btn = tk.Button(bar, text="Escanear", width=14, font=("Segoe UI", 10, "bold"))
    save = tk.Button(bar, text="Guardar informe", state="disabled")
    win = ttk.Combobox(bar, values=list(WINDOWS), width=22)
    win.set("Ultimos 7 dias")
    btn.pack(side="left")
    save.pack(side="left", padx=6)
    tk.Label(bar, text="Eventos:").pack(side="left", padx=(10, 2))
    win.pack(side="left")
    status.pack(side="left", padx=10, fill="x", expand=True)
    txt = scrolledtext.ScrolledText(root, font=("Consolas", 10), wrap="word")
    txt.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    for k, c in colors.items():
        txt.tag_config(k, foreground=c, font=("Consolas", 10, "bold"))
    txt.tag_config("h", font=("Consolas", 11, "bold"), foreground="#202124")
    txt.tag_config("dim", foreground="#5f6368")
    state = {"lines": []}

    def scan():
        since = parse_since(win.get())
        if since is None:
            status.config(text="Ventana invalida. Usa la lista o 'AAAA-MM-DD HH:MM'.")
            return
        btn.config(state="disabled")
        save.config(state="disabled")
        txt.delete("1.0", "end")

        def work():
            try:
                d = collect(lambda m: root.after(0, status.config, {"text": m}), since)
                v, vt, lines = build_report(d)
                root.after(0, show, v, vt, lines)
            except Exception as e:
                root.after(0, status.config, {"text": f"Error: {e}"})
                root.after(0, btn.config, {"state": "normal"})
        threading.Thread(target=work, daemon=True).start()

    def show(v, vt, lines):
        state["lines"] = lines
        banner.config(text=vt, bg=colors[v])
        for tag, s in lines:
            txt.insert("end", s + "\n", tag or ())
        status.config(text="Listo.")
        btn.config(state="normal")
        save.config(state="normal")

    def do_save():
        p = filedialog.asksaveasfilename(defaultextension=".txt",
                                         initialfile=f"informe_red_{datetime.now():%Y%m%d_%H%M}.txt")
        if p:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("\n".join(s for _, s in state["lines"]))

    btn.config(command=scan)
    save.config(command=do_save)
    root.mainloop()


if __name__ == "__main__":
    if "--cli" in sys.argv:
        arg = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else None
        since = parse_since(arg) if arg else None
        if arg and since is None:
            sys.exit("--since debe ser 'AAAA-MM-DD HH:MM'")
        data = collect(lambda m: print(m, file=sys.stderr), since)
        v, vt, lines = build_report(data)
        sys.stdout.reconfigure(encoding="utf-8")
        print(f"== {vt} ==")
        for _, s in lines:
            print(s)
    else:
        run_gui()
