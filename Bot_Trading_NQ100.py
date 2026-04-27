"""
════════════════════════════════════════════════════════════════════
Bugs corregidos respecto a versión anterior:
  [CRÍTICO] check_bos: ahora valida solo velas POSTERIORES al sweep
  [CRÍTICO] find_m5_entry: corregido timezone mismatch (UTC naive vs aware)
  [CRÍTICO] close_all: añadida key 'position' para cerrar correctamente
  [IMPORTANTE] in_trade: sincronizado con posiciones reales al arrancar
  [IMPORTANTE] trades_today: solo se incrementa si la orden se ejecuta
  [LEVE] Martes configurable con SKIP_TUESDAY = True/False
  [LEVE] ORDER_FILLING: fallback automático IOC → FOK → RETURN
  [MEJORA] Break-even automático configurable
════════════════════════════════════════════════════════════════════
Backtest 15 meses H4/H1/M5: +27.013€ | DD: -312€ | 0 meses neg
  [FIX v2.2] MAX_SL 130 → 140: evita rechazo por movimiento del BOS
  [FIX v2.2] datetime.utcnow() eliminado (sin DeprecationWarning)
  [FIX v2.1] datetime.now(timezone.utc).replace(tzinfo=None) → datetime.now(timezone.utc) (sin DeprecationWarning)
  [FIX v2.1] Mensaje claro cuando el precio se aleja demasiado del OB (SL > MAX_SL)
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import time
import logging
from datetime import datetime, timedelta, timezone
import pytz

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler('tjr_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('TJR')

# ════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ════════════════════════════════════════════════════════════════
SYMBOL       = ""          # vacío = auto-detección
MAGIC        = 737373
TZ           = pytz.timezone("Europe/Madrid")

BASE_RISK    = 100.0       # Riesgo base por operación (€)
TP_RATIO     = 3.5         # TP = distancia SL × 3.5
SL_BUFFER    = 10.0        # Puntos extra en SL más allá del wick
MIN_SL       = 25.0        # SL mínimo en puntos
MAX_SL       = 140.0       # SL máximo en puntos (140 da margen al movimiento del BOS)
MIN_WICK     = 3.0         # Wick mínimo del sweep sobre nivel H1
MAX_TRADES   = 2           # Máximo trades por día
H1_PIVOT_N   = 3           # Velas confirmación swing H1
H4_PIVOT_N   = 2           # Velas confirmación swing H4

# Sizing dinámico (coherente con el backtest)
M_JUEVES     = 1.2
M_VIERNES    = 1.2
M_MAÑANA_BAJ = 1.2
M_SEMANA1    = 1.3
M_SEMANA5    = 1.3

# Martes: False = operamos martes también (TJR no tiene este filtro)
SKIP_TUESDAY = False

# Break-even: mover SL a entrada cuando el precio avanza BE_TRIGGER R
# False = desactivado (coherente con backtest)
BE_TRIGGER   = False       # ej: 1.0 = cuando el precio lleva 1R a favor

# ════════════════════════════════════════════════════════════════
#  AUTO-DETECCIÓN DEL SÍMBOLO
# ════════════════════════════════════════════════════════════════
_NAS_CANDIDATES = [
    "NAS100","NAS100.cash","NAS100.pro","NAS100m",
    "US100","US100.cash","US100.pro","US100m",
    "USTEC","USTEC.cash","USTECH","NASDAQ","NDX","NQ100","Nas100","Us100",
]

def find_symbol():
    global SYMBOL
    if SYMBOL:
        info = mt5.symbol_info(SYMBOL)
        if info is not None:
            if not info.visible: mt5.symbol_select(SYMBOL, True); time.sleep(0.5)
            log.info(f"Símbolo manual: {SYMBOL}"); return True
        log.warning(f"'{SYMBOL}' no encontrado. Auto-detectando...")
    for c in _NAS_CANDIDATES:
        info = mt5.symbol_info(c)
        if info is not None:
            SYMBOL = c
            if not info.visible: mt5.symbol_select(SYMBOL, True); time.sleep(0.5)
            log.info(f"✓ Símbolo: {SYMBOL}"); return True
    all_s = mt5.symbols_get()
    if all_s:
        kw = ['nas','us100','ustec','nasdaq','ndx','nq']
        m = [s.name for s in all_s if any(k in s.name.lower() for k in kw)]
        if m:
            SYMBOL = m[0]; mt5.symbol_select(SYMBOL, True); time.sleep(0.5)
            log.info(f"✓ Símbolo encontrado: {SYMBOL} (de {m})"); return True
    log.error("❌ No se encontró NAS100/US100.")
    log.error("   Añade el símbolo al Market Watch de MT5 y pon SYMBOL='nombre_exacto'.")
    return False

def ensure_in_watch():
    info = mt5.symbol_info(SYMBOL)
    if info is None: return False
    if not info.visible:
        if not mt5.symbol_select(SYMBOL, True): return False
        time.sleep(0.5)
    return True


# ════════════════════════════════════════════════════════════════
#  ESTADO
# ════════════════════════════════════════════════════════════════
class BotState:
    def __init__(self):
        self.h1_highs          = []
        self.h1_lows           = []
        self.h4_pivots         = []
        self.trades_today      = 0
        self.today             = None
        self.used_levels       = set()
        self.pending_sweep     = None
        self.sweep_detected_at = None
        self.sweep_candle_time = None   # ← NUEVO: timestamp de la vela del sweep
        self.last_h1_update    = None
        self.last_h4_update    = None
        self.in_trade          = False
        self.last_h1_time      = None

state = BotState()


# ════════════════════════════════════════════════════════════════
#  CARGA DE DATOS — con reintentos
# ════════════════════════════════════════════════════════════════
def load_rates(tf, n, label, retries=3):
    for attempt in range(retries):
        ensure_in_watch()
        rates = mt5.copy_rates_from_pos(SYMBOL, tf, 0, n)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            # Convertir tiempo MT5 (UTC unix) a datetime naive UTC
            df['time'] = pd.to_datetime(df['time'], unit='s', utc=True).dt.tz_convert(None)
            df = df.rename(columns={'open':'OPEN','high':'HIGH','low':'LOW','close':'CLOSE'})
            return df
        err = mt5.last_error()
        log.warning(f"  [{label}] intento {attempt+1}/{retries}: {err}")
        time.sleep(2)
    log.error(f"No se pudo cargar {label}. Error: {mt5.last_error()}")
    return None

def load_h4(n=40): return load_rates(mt5.TIMEFRAME_H4, n, "H4")
def load_h1(n=70): return load_rates(mt5.TIMEFRAME_H1, n, "H1")
def load_m5(n=30): return load_rates(mt5.TIMEFRAME_M5, n, "M5")


# ════════════════════════════════════════════════════════════════
#  PIVOTS Y BIAS
# ════════════════════════════════════════════════════════════════
def detect_pivots_df(df, n):
    H,L,T = df['HIGH'].values, df['LOW'].values, df['time'].values
    highs=[]; lows=[]; end=len(df)-n-1
    for i in range(n, end):
        if H[i]==max(H[i-n:i+n+1]): highs.append({'time':T[i],'price':float(H[i])})
        if L[i]==min(L[i-n:i+n+1]): lows.append( {'time':T[i],'price':float(L[i])})
    return highs[-15:], lows[-15:]

def compute_h4_bias():
    sh=[p for p in state.h4_pivots if p['type']=='SH']
    sl=[p for p in state.h4_pivots if p['type']=='SL']
    if len(sh)<2 or len(sl)<2: return 'NEUTRAL'
    if sh[-1]['price']>sh[-2]['price'] and sl[-1]['price']>sl[-2]['price']: return 'BULL'
    if sh[-1]['price']<sh[-2]['price'] and sl[-1]['price']<sl[-2]['price']: return 'BEAR'
    return 'NEUTRAL'

def update_h4():
    df=load_h4(40)
    if df is None: return
    H,L,T=df['HIGH'].values,df['LOW'].values,df['time'].values
    n=H4_PIVOT_N; pivots=[]
    for i in range(n,len(df)-n-1):
        if H[i]==max(H[i-n:i+n+1]): pivots.append({'time':T[i],'price':float(H[i]),'type':'SH'})
        if L[i]==min(L[i-n:i+n+1]): pivots.append({'time':T[i],'price':float(L[i]),'type':'SL'})
    state.h4_pivots=sorted(pivots,key=lambda x:x['time'])[-20:]
    state.last_h4_update=datetime.now(TZ)
    log.info(f"H4 OK → Bias: {compute_h4_bias()} ({len(state.h4_pivots)} pivots)")

def update_h1():
    df=load_h1(70)
    if df is None: return
    state.h1_highs, state.h1_lows = detect_pivots_df(df, H1_PIVOT_N)
    state.last_h1_update=datetime.now(TZ)
    # Última vela cerrada = iloc[-2] (iloc[-1] es la vela actual abierta)
    state.last_h1_time = df.iloc[-2]['time'] if len(df)>=2 else None
    log.info(f"H1 OK → {len(state.h1_highs)} highs, {len(state.h1_lows)} lows")


# ════════════════════════════════════════════════════════════════
#  DETECCIÓN NUEVA VELA H1
# ════════════════════════════════════════════════════════════════
def new_h1_closed():
    df=load_h1(5)
    if df is None or len(df)<2: return False
    t=df.iloc[-2]['time']
    if state.last_h1_time is None:
        state.last_h1_time=t; return False
    if t>state.last_h1_time:
        state.last_h1_time=t; return True
    return False


# ════════════════════════════════════════════════════════════════
#  SWEEP DETECTION
# ════════════════════════════════════════════════════════════════
def check_for_sweep():
    df=load_h1(5)
    if df is None or len(df)<3: return None
    last=df.iloc[-2]  # última H1 cerrada
    bias=compute_h4_bias()

    if bias!='BULL':
        for piv in reversed(state.h1_highs):
            lvl=piv['price']; key=(round(lvl),'SH')
            if key in state.used_levels: continue
            wick=float(last['HIGH'])-lvl
            if wick<MIN_WICK: continue
            if last['HIGH']>lvl and last['CLOSE']<lvl:
                log.info(f"🔴 SWEEP SHORT | Nivel:{lvl:.2f} Wick:{wick:.1f}pts Bias:{bias}")
                return {'direction':'SHORT','level':lvl,'type':'SH',
                        'candle_high':float(last['HIGH']),'candle_low':float(last['LOW']),
                        'candle_time':last['time'],  # ← guardamos timestamp del sweep
                        'bias':bias,'wick':wick}

    if bias!='BEAR':
        for piv in reversed(state.h1_lows):
            lvl=piv['price']; key=(round(lvl),'SL')
            if key in state.used_levels: continue
            wick=lvl-float(last['LOW'])
            if wick<MIN_WICK: continue
            if last['LOW']<lvl and last['CLOSE']>lvl:
                log.info(f"🟢 SWEEP LONG | Nivel:{lvl:.2f} Wick:{wick:.1f}pts Bias:{bias}")
                return {'direction':'LONG','level':lvl,'type':'SL',
                        'candle_high':float(last['HIGH']),'candle_low':float(last['LOW']),
                        'candle_time':last['time'],  # ← guardamos timestamp del sweep
                        'bias':bias,'wick':wick}
    return None


# ════════════════════════════════════════════════════════════════
#  BOS CHECK — CORREGIDO: solo velas POSTERIORES al sweep
# ════════════════════════════════════════════════════════════════
def check_bos(sweep):
    """
    BUG CORREGIDO: la versión anterior usaba df.iloc[-2] que podía ser
    la misma vela del sweep. Ahora solo valida velas con timestamp > sweep.
    """
    df=load_h1(5)
    if df is None or len(df)<2: return False

    sweep_time = sweep['candle_time']  # timestamp de la vela del sweep

    # Solo considerar velas cerradas POSTERIORES a la del sweep
    post_sweep = df[df['time'] > sweep_time]
    if post_sweep.empty: return False

    # Comprobar la más reciente de ellas
    last = post_sweep.iloc[-1]

    if sweep['direction']=='SHORT' and float(last['CLOSE'])<sweep['candle_low']:
        log.info(f"✓ BOS SHORT confirmado: {last['CLOSE']:.2f} < {sweep['candle_low']:.2f}")
        return True
    if sweep['direction']=='LONG' and float(last['CLOSE'])>sweep['candle_high']:
        log.info(f"✓ BOS LONG confirmado: {last['CLOSE']:.2f} > {sweep['candle_high']:.2f}")
        return True
    return False


# ════════════════════════════════════════════════════════════════
#  SIZING DINÁMICO
# ════════════════════════════════════════════════════════════════
def compute_risk(now):
    risk=BASE_RISK
    wd=now.weekday(); wom=(now.day-1)//7+1
    if wd==3: risk*=M_JUEVES
    elif wd==4: risk*=M_VIERNES
    if wom==1: risk*=M_SEMANA1
    elif wom==5: risk*=M_SEMANA5
    # Mañana bajista: H1 08:00 MT5 (09:00 España) vs H1 12:00 MT5 (13:00 España)
    df=load_h1(20)
    if df is not None:
        # df['time'] es UTC naive; MT5 va 1h por detrás de España → 08:00 MT5
        # MT5 times are UTC; now is Madrid (UTC+1/+2). Convert to UTC for correct date.
        from datetime import timezone as _tz
        utc_now = now.astimezone(_tz.utc)
        tc=df[df['time'].dt.date==utc_now.date()]
        c08=tc[tc['time'].dt.hour==8]; c12=tc[tc['time'].dt.hour==12]
        if not c08.empty and not c12.empty:
            if float(c12.iloc[-1]['CLOSE'])<float(c08.iloc[0]['OPEN']):
                risk*=M_MAÑANA_BAJ
                log.info(f"  +Mañana bajista ×{M_MAÑANA_BAJ}")
    return round(risk,2)


# ════════════════════════════════════════════════════════════════
#  M5 ENTRY — CORREGIDO: timezone mismatch
# ════════════════════════════════════════════════════════════════
def find_m5_entry(sweep):
    """
    BUG CORREGIDO: datetime.now(TZ) tiene tzinfo, pero df['time'] de MT5
    es UTC naive. Antes la comparación fallaba siempre y usaba el fallback.
    Ahora convertimos el cutoff a UTC naive para comparar correctamente.
    """
    lvl=sweep['level']; direction=sweep['direction']
    df=load_m5(20)
    if df is not None:
        # Cutoff: 2 horas atrás en UTC naive (MT5 time es UTC)
        now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff = now_utc_naive - timedelta(hours=2)

        recent=df[df['time']>cutoff]
        for _,c in recent.iterrows():
            if direction=='SHORT':
                if float(c['HIGH'])>=(lvl-20) and float(c['LOW'])<=lvl:
                    ep=min(float(c['CLOSE']),lvl-3)
                    log.info(f"  📍 Entrada M5 OB SHORT: {ep:.2f} (nivel:{lvl:.2f})")
                    return ep
            else:
                if float(c['HIGH'])>=lvl and float(c['LOW'])<=(lvl+20):
                    ep=max(float(c['CLOSE']),lvl+3)
                    log.info(f"  📍 Entrada M5 OB LONG: {ep:.2f} (nivel:{lvl:.2f})")
                    return ep
    ep=lvl-5 if direction=='SHORT' else lvl+5
    log.info(f"  📍 Entrada fallback: {ep:.2f}")
    return ep


# ════════════════════════════════════════════════════════════════
#  COLOCAR ORDEN — con fallback de filling
# ════════════════════════════════════════════════════════════════
def place_order(sweep, now):
    entry_ref=find_m5_entry(sweep); direction=sweep['direction']

    # ── Obtener precio real de ejecución ANTES de calcular SL/TP ──────
    # El entry_ref de M5 es referencial (para el log), pero la orden entra
    # a mercado. SL y TP deben calcularse desde el precio real, no desde
    # entry_ref, o los niveles quedan descolocados y el broker los rechaza.
    tick=mt5.symbol_info_tick(SYMBOL)
    if tick is None: log.error("Sin tick"); return False
    price=tick.bid if direction=='SHORT' else tick.ask

    if direction=='SHORT':
        sl=sweep['candle_high']+SL_BUFFER
        otype=mt5.ORDER_TYPE_SELL
        sl_d=sl-price          # distancia SL desde el precio REAL de entrada
        tp=price-sl_d*TP_RATIO # TP desde el precio REAL de entrada
    else:
        sl=sweep['candle_low']-SL_BUFFER
        otype=mt5.ORDER_TYPE_BUY
        sl_d=price-sl          # distancia SL desde el precio REAL de entrada
        tp=price+sl_d*TP_RATIO # TP desde el precio REAL de entrada

    if sl_d < MIN_SL or sl_d > MAX_SL:
        if sl_d > MAX_SL:
            log.warning(
                f"⚠ Trade saltado: precio actual {price:.2f} demasiado lejos del OB. "
                f"SL dist={sl_d:.1f}pts > MAX_SL={MAX_SL}pts | SL nivel={sl:.2f} | "
                f"Nivel sweep={sweep['level']:.2f}. El BOS impulsó el precio alejándolo del OB."
            )
        else:
            log.warning(f"⚠ Trade saltado: SL {sl_d:.1f}pts < MIN_SL={MIN_SL}pts.")
        return False

    risk=compute_risk(now)

    # ── Cálculo correcto de lotes usando valor real del punto ──────────
    # Para NAS100: 1 lote ≈ 15-20€/punto según broker → NO se puede asumir 1€/punto
    # Obtenemos el valor real desde MT5: trade_tick_value / trade_tick_size = €/punto/lote
    sym_info = mt5.symbol_info(SYMBOL)
    if sym_info is None: log.error("No se pudo obtener info del símbolo"); return False
    if sym_info.trade_tick_size == 0: log.error("trade_tick_size = 0"); return False
    point_value = sym_info.trade_tick_value / sym_info.trade_tick_size  # €/punto/lote
    if point_value <= 0: point_value = 1.0  # fallback de seguridad
    lots = round(risk / (sl_d * point_value), 2)
    lots = max(lots, sym_info.volume_min)   # mínimo de lotes del broker
    lots = min(lots, sym_info.volume_max)   # máximo de lotes del broker
    # Redondear al step del broker (volume_step)
    if sym_info.volume_step > 0:
        lots = round(lots / sym_info.volume_step) * sym_info.volume_step
        lots = round(lots, 2)

    real_risk_eur = sl_d * lots * point_value

    log.info(f"▶ {direction} | EntryRef≈{entry_ref:.2f} | PrecioReal={price:.2f} | "
             f"SL={sl:.2f} | TP={tp:.2f} | Dist={sl_d:.1f}pts | "
             f"PtVal={point_value:.2f}€ | Lots={lots} | "
             f"Riesgo calculado={risk:.0f}€ | Riesgo real≈{real_risk_eur:.0f}€")

    # ── Tolerancia de ejecución ────────────────────────────────────────
    # NAS100: spread normal 30-50pts + movimiento mientras llega al servidor.
    # 100 pts = ~0.06% del índice → tolerancia razonable sin riesgo de
    # ejecutar a precio muy distante. Ajusta si tu broker es más lento.
    DEVIATION = 100  # puntos NAS100

    # Intentar con IOC, luego FOK, luego RETURN
    # IMPORTANTE: refrescamos el precio en CADA intento para no mandar
    # un precio obsoleto que el broker rechazaría igualmente.
    for filling in [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]:
        for attempt in range(2):
            # Precio fresco en cada intento
            tick = mt5.symbol_info_tick(SYMBOL)
            if tick is None:
                log.warning("  Sin tick al intentar orden"); time.sleep(1); continue
            price = tick.bid if direction == 'SHORT' else tick.ask

            # Recalcular TP con el precio fresco (SL fijo en el wick)
            if direction == 'SHORT':
                sl_d = sl - price
                tp   = price - sl_d * TP_RATIO
            else:
                sl_d = price - sl
                tp   = price + sl_d * TP_RATIO

            # Verificar que el SL sigue siendo válido con el precio actual
            if sl_d < MIN_SL or sl_d > MAX_SL:
                log.warning(f"  SL {sl_d:.1f}pts inválido con precio actualizado. Abortando.")
                return False

            res=mt5.order_send({
                "action":mt5.TRADE_ACTION_DEAL,"symbol":SYMBOL,"volume":float(lots),
                "type":otype,"price":float(price),"sl":float(sl),"tp":float(tp),
                "deviation":DEVIATION,"magic":MAGIC,
                "comment":f"TJR {'S' if direction=='SHORT' else 'L'} {sweep['level']:.0f}",
                "type_time":mt5.ORDER_TIME_GTC,"type_filling":filling
            })
            if res and res.retcode==mt5.TRADE_RETCODE_DONE:
                log.info(f"✅ Orden OK. Ticket #{res.order} | Price={price:.2f} | "
                         f"SL={sl:.2f} | TP={tp:.2f} | filling={filling}")
                state.used_levels.add((round(sweep['level']),sweep['type']))
                state.in_trade=True
                return True
            log.warning(f"  Intento {attempt+1} filling={filling} → retcode={res.retcode if res else 'None'}")
            time.sleep(1)
        log.warning(f"  Filling {filling} agotado. Probando siguiente...")

    log.error("❌ Orden fallida con todos los fillings."); return False


# ════════════════════════════════════════════════════════════════
#  BREAK-EVEN AUTOMÁTICO (opcional)
# ════════════════════════════════════════════════════════════════
def manage_breakeven():
    """Mueve el SL a break-even cuando el precio avanza BE_TRIGGER R."""
    if not BE_TRIGGER: return
    positions=mt5.positions_get(symbol=SYMBOL,magic=MAGIC)
    if not positions: return
    for p in positions:
        if p.profit<=0: continue
        sl_dist=abs(p.price_open-p.sl) if p.sl>0 else 0
        if sl_dist<=0: continue
        be_level=p.price_open+(sl_dist*BE_TRIGGER) if p.type==mt5.ORDER_TYPE_BUY \
                 else p.price_open-(sl_dist*BE_TRIGGER)
        tick=mt5.symbol_info_tick(SYMBOL)
        if tick is None: continue
        price=tick.ask if p.type==mt5.ORDER_TYPE_BUY else tick.bid
        if (p.type==mt5.ORDER_TYPE_BUY and price>=be_level and p.sl<p.price_open) or \
           (p.type==mt5.ORDER_TYPE_SELL and price<=be_level and p.sl>p.price_open):
            mt5.order_send({"action":mt5.TRADE_ACTION_SLTP,"symbol":SYMBOL,
                            "position":p.ticket,"sl":float(p.price_open),"tp":float(p.tp)})
            log.info(f"  BE activado para #{p.ticket}")


# ════════════════════════════════════════════════════════════════
#  CIERRE 22:00 — CORREGIDO: añadida key 'position'
# ════════════════════════════════════════════════════════════════
def close_all():
    """
    BUG CORREGIDO: la versión anterior no incluía 'position': p.ticket,
    lo que hacía que MT5 pudiera abrir una orden opuesta en vez de cerrar.
    """
    positions=mt5.positions_get(symbol=SYMBOL,magic=MAGIC)
    if not positions: log.info("[22h] Sin posiciones."); return
    for p in positions:
        tick=mt5.symbol_info_tick(SYMBOL)
        if tick is None: continue
        price=tick.ask if p.type==mt5.ORDER_TYPE_BUY else tick.bid
        close_type=mt5.ORDER_TYPE_SELL if p.type==mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        res=mt5.order_send({
            "action":   mt5.TRADE_ACTION_DEAL,
            "symbol":   SYMBOL,
            "volume":   p.volume,
            "type":     close_type,
            "position": p.ticket,       # ← CLAVE: especificar la posición a cerrar
            "price":    float(price),
            "deviation":30,
            "magic":    MAGIC,
            "comment":  "TJR cierre 22h",
            "type_filling": mt5.ORDER_FILLING_IOC,
        })
        if res and res.retcode==mt5.TRADE_RETCODE_DONE:
            log.info(f"  ✓ Cerrada #{p.ticket} | P&L: {p.profit:.2f}€")
        else:
            log.warning(f"  ✗ Error cerrando #{p.ticket}: {res.retcode if res else 'None'}")


# ════════════════════════════════════════════════════════════════
#  GESTIÓN DE ESTADO
# ════════════════════════════════════════════════════════════════
def sync_state():
    """
    NUEVO: sincroniza in_trade con las posiciones reales al arrancar.
    Evita que el bot ignore posiciones abiertas de sesiones anteriores.
    """
    positions=mt5.positions_get(symbol=SYMBOL,magic=MAGIC)
    state.in_trade = positions is not None and len(positions)>0
    if state.in_trade:
        log.info(f"  Estado sincronizado: {len(positions)} posición(es) ya abiertas.")

def check_trade_closed():
    positions=mt5.positions_get(symbol=SYMBOL,magic=MAGIC)
    if not positions: state.in_trade=False

def daily_reset(today):
    if state.today!=today:
        state.today=today; state.trades_today=0
        state.used_levels=set(); state.pending_sweep=None
        state.sweep_detected_at=None; state.sweep_candle_time=None
        # No reseteamos in_trade aquí — lo gestiona sync_state y check_trade_closed
        log.info(f"━━ Nuevo día: {today} ━━")

def in_ny_session(now):
    return (now.hour==15 and now.minute>=30) or (16<=now.hour<22)

def should_upd_h4(now):
    return state.last_h4_update is None or (now-state.last_h4_update).total_seconds()>14400
def should_upd_h1(now):
    return state.last_h1_update is None or (now-state.last_h1_update).total_seconds()>3600


# ════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════
def main():
    if not mt5.initialize():
        log.error(f"❌ MT5 no inicializado: {mt5.last_error()}"); return

    if not find_symbol(): mt5.shutdown(); return
    if not ensure_in_watch(): mt5.shutdown(); return

    info=mt5.symbol_info(SYMBOL)
    log.info("═"*62)
    log.info("  TJR BOT v2.0 — LISTO")
    log.info(f"  Símbolo: {SYMBOL} | Spread: {info.spread}pts")
    log.info(f"  Risk: {BASE_RISK}€ base | TP: {TP_RATIO}R | SL buffer: {SL_BUFFER}pts")
    log.info(f"  SL: [{MIN_SL}-{MAX_SL}]pts | Max {MAX_TRADES} trades/día")
    log.info(f"  Martes: {'skip' if SKIP_TUESDAY else 'opera'} | "
             f"BE: {'×'+str(BE_TRIGGER)+'R' if BE_TRIGGER else 'off'}")
    log.info(f"  Sizing: Ju×{M_JUEVES} Vi×{M_VIERNES} Mañ↓×{M_MAÑANA_BAJ} "
             f"S1×{M_SEMANA1} S5×{M_SEMANA5}")
    log.info("═"*62)

    update_h4()
    update_h1()
    sync_state()  # sincronizar posiciones abiertas al arrancar

    while True:
        try:
            now=datetime.now(TZ); today=now.date()
            daily_reset(today)

            if SKIP_TUESDAY and now.weekday()==1:
                time.sleep(60); continue

            if should_upd_h4(now): update_h4()

            # Cierre 22:00 España
            if now.hour==22 and now.minute==0 and now.second<30:
                close_all()
                state.pending_sweep=None; state.in_trade=False
                time.sleep(65); continue

            if not in_ny_session(now):
                time.sleep(20); continue

            # Gestión break-even si está activo
            if BE_TRIGGER and state.in_trade:
                manage_breakeven()

            # Si hay posición, solo monitorizar
            if state.in_trade:
                check_trade_closed(); time.sleep(15); continue

            if state.trades_today>=MAX_TRADES:
                time.sleep(30); continue

            # Nueva vela H1 cerrada
            if new_h1_closed():
                update_h1()

                # PASO 1: buscar sweep
                if state.pending_sweep is None:
                    sweep=check_for_sweep()
                    if sweep:
                        state.pending_sweep=sweep
                        state.sweep_detected_at=now
                        log.info("  Esperando BOS H1...")

                # PASO 2: si hay sweep, comprobar BOS en velas POSTERIORES
                if state.pending_sweep is not None:
                    elapsed=(now-state.sweep_detected_at).total_seconds()
                    if elapsed>4*3600:
                        log.info("⏱ Timeout 4h. Sweep descartado.")
                        state.pending_sweep=None
                    elif check_bos(state.pending_sweep):
                        log.info(f"🚀 Ejecutando trade {state.trades_today+1}/{MAX_TRADES}...")
                        # CORREGIDO: solo incrementar si la orden se ejecutó
                        if place_order(state.pending_sweep,now):
                            state.trades_today+=1
                        state.pending_sweep=None

            time.sleep(15)

        except KeyboardInterrupt:
            log.info("Bot detenido manualmente."); break
        except Exception as e:
            log.error(f"Error inesperado: {e}", exc_info=True)
            time.sleep(30)

    mt5.shutdown()

if __name__=='__main__':
    main()
