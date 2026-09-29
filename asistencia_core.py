"""
asistencia_core — la lógica de la asistencia por MAC, sin base de datos.

POR QUÉ EXISTE ESTE MÓDULO
--------------------------
`eccsa_db.calcular_asistencia_dia` mezclaba el SQL, los permisos y las reglas
de puntualidad en una sola función de 125 líneas que no se podía ni probar sin
levantar una base de datos. Todo lo que decide si alguien "llegó tarde" o
"estuvo ausente" —que es lo que de verdad importa— vive aquí, en funciones
puras: entran horas, sale un veredicto. Así hay pruebas que corren en un
segundo, sin base de datos, y el SQL queda siendo lo que es: traer datos.

LA IDEA
-------
El escáner mira la red cada N minutos. Cuando aparece el MAC de alguien, lo
que se sabe de verdad NO es "llegó a las 9:02", sino esto:

    la persona llegó en algún momento entre
        piso  = la última vez que se vio su MAC ausente
        techo = la primera vez que se vio su MAC presente

Con un escaneo cada 3 minutos ese intervalo mide 3 minutos. Guardar un solo
minuto (el techo, como se guardaba antes) es afirmar una precisión que no
existe; y comparar esa afirmación contra la tolerancia produce dos errores
reales: marca "tarde" a quien llegó puntual, o deja pasar al que llegó 5
minutos tarde porque el techo cayó dentro de la tolerancia.

Por eso el veredicto sale de la VENTANA, no de un punto:

    TARDE          si piso > esperada + tolerancia   (no hay duda: llegó tarde)
    A_SALVO        si techo <= esperada + tolerancia (no se puede afirmar nada)
    INDETERMINADO  si la ventana CRUZA la tolerancia

En el caso intermedio se dice "no se puede saber", que es la verdad. Ninguna
de las tres inventa precisión. La política (PISO / TECHO / PROMEDIO) decide
qué minuto se anota para nómina, y se cambia sin recalcular el histórico.

Y lo que NO se hace, porque son los falsos positivos que sí hacen daño:
    · si el escáner no escaneó, el día es SIN DATOS — no AUSENTE;
    · si ese día no había turno, es NO_APLICA — no AUSENTE;
    · la presencia fuera de la ventana del turno no cuenta como entrada.
"""

from datetime import date, datetime, time, timedelta

# ── Estados ─────────────────────────────────────────────────────────────────
# Los tres últimos son los que evitan los falsos positivos: antes TODO lo que
# no fuera "presente con entrada" caía en AUSENTE, incluida una caída del
# escáner y un sábado sin turno configurado.
ESTADO_CALCULADA = "CALCULADA"
ESTADO_SIN_DATOS = "SIN_DATOS"            # el escáner no cubrió el día
ESTADO_AUSENTE = "AUSENTE"                # se escaneó y no hubo presencia
ESTADO_NO_APLICA = "NO_APLICA"            # ese día no había turno
ESTADO_INDETERMINADO = "INDETERMINADO"    # la ventana cruza la tolerancia
ESTADO_TARDE = "TARDE"
ESTADO_SALIDA_TEMPRANA = "SALIDA_TEMPRANA"

VEREDICTO_OK = "A_SALVO"
VEREDICTO_TARDE = "TARDE"
VEREDICTO_INDETERMINADO = "INDETERMINADO"

POLITICA_PISO = "PISO"          # el extremo que favorece al trabajador
POLITICA_TECHO = "TECHO"        # el extremo más estricto
POLITICA_PROMEDIO = "PROMEDIO"  # el punto medio de la ventana


def limpiar_hora(valor):
    """
    Normaliza lo que devuelve el driver a un `time` (o None).

    pymssql devuelve TIME como `datetime.time`, pero según el driver y la
    versión puede llegar como `datetime` (con la fecha 1899-12-30 de fondo) o
    como `timedelta`. Se resuelven los tres casos aquí, en un solo lugar: si se
    cuela un `datetime` en una resta de horas, la comparación revienta.
    """
    if valor is None:
        return None
    if isinstance(valor, time):
        return valor
    if isinstance(valor, datetime):
        return valor.time()
    if isinstance(valor, timedelta):
        return (datetime.min + valor).time()
    if isinstance(valor, str):
        partes = valor.strip().split(":")
        try:
            return time(int(partes[0]), int(partes[1]),
                        int(partes[2]) if len(partes) > 2 else 0)
        except (ValueError, IndexError):
            return None
    return None


def formatear_hora(hora):
    """'09:02' o '—'. Para las columnas de la vista y el PDF."""
    h = limpiar_hora(hora)
    return f"{h.hour:02d}:{h.minute:02d}" if h else "—"


def formatear_ventana(piso, techo):
    """'08:57–09:00 ±3 min', o solo un extremo si no hay evidencia completa."""
    a, b = limpiar_hora(piso), limpiar_hora(techo)
    if a and b:
        ancho = (b.hour * 60 + b.minute) - (a.hour * 60 + a.minute)
        if ancho < 0:
            ancho += 24 * 60
        sufijo = " ±0 min" if ancho == 0 else f" ±{ancho} min"
        return f"{formatear_hora(a)}–{formatear_hora(b)}{sufijo}"
    if a or b:
        return f"{formatear_hora(a or b)} ±?"
    return "—"


def _minutos(hora):
    """Hora → minutos desde medianoche (acepta time/datetime/str)."""
    h = limpiar_hora(hora)
    if h is None:
        return None
    return h.hour * 60 + h.minute


def clasificar_ventana(piso, techo, esperada, tolerancia_min, desfavorable=1):
    """
    Dict con el veredicto de una ventana contra un horario esperado.

    `piso`/`techo` acotan el instante real (en ese orden, siempre). `esperada`
    es la hora del turno. `desfavorable` es +1 cuando lo que se castiga es
    llegar TARDE (minutos que se pasa de la esperada) y -1 cuando lo que se
    castiga es irse ANTES (minutos que le falta para la esperada). Con esa
    sola bandera, la entrada y la salida se evaluan con el mismo código.

    Devuelve:
        veredicto     A_SALVO | TARDE | INDETERMINADO
        desfavor_piso  minutos malo en el extremo temprano de la ventana
        desfavor_techo minutos malo en el extremo tardío
        ancho_min     qué tan ancho es el dato (0 = tan exacto como el muestreo)
    """
    m_piso, m_techo, m_esp = _minutos(piso), _minutos(techo), _minutos(esperada)
    if m_piso is None or m_techo is None or m_esp is None:
        return {
            "veredicto": VEREDICTO_INDETERMINADO,
            "desfavor_piso": None,
            "desfavor_techo": None,
            "ancho_min": None,
            "aplicado": None,
            "motivo": "sin evidencia suficiente",
        }

    tol = int(tolerancia_min or 0)

    # Turno que cruza la medianoche: si el techo es anterior al piso, la
    # ventana pasó de las 24:00 y hay que desempaquetarla, o la resta sale
    # negativa y todo el mundo parece puntual.
    cruza = m_techo < m_piso
    if cruza:
        m_techo += 24 * 60
        if m_esp < m_piso:
            m_esp += 24 * 60

    mal_piso = desfavorable * (m_piso - m_esp)
    mal_techo = desfavorable * (m_techo - m_esp)

    # El extremo "peor" no siempre es el mismo: en la entrada lo peor es el
    # techo (llegó lo más tarde posible) y en la salida lo peor es el piso (se
    # fue lo más antes posible). Por eso se ordena antes de decidir, en vez de
    # asumir que el piso siempre es el peor: hacerlo dejaba la banda
    # "indeterminado" VACÍA en las salidas, o sea que toda salida anticipada se
    # cobraba en su lectura más estricta, sin beneficio de la duda.
    peor, mejor = max(mal_piso, mal_techo), min(mal_piso, mal_techo)

    if mejor > tol:
        # Ni en el extremo más favorable se llega a la tolerancia: es afirmable.
        veredicto = VEREDICTO_TARDE
    elif peor > tol:
        # La ventana CRUZA la tolerancia: con esta evidencia no se puede decir.
        veredicto = VEREDICTO_INDETERMINADO
    else:
        veredicto = VEREDICTO_OK

    return {
        "veredicto": veredicto,
        "desfavor_piso": mal_piso,
        "desfavor_techo": mal_techo,
        "ancho_min": m_techo - m_piso,
        "cruzando_medianoche": cruza,
    }


def aplicar_politica(ventana, politica=POLITICA_PISO):
    """
    Qué minuto se anota cuando la ventana es más ancha que la tolerancia.

    PISO     el extremo que FAVORECE al trabajador (la prueba de que aún no
             había llegado). Es el que evita "llegó tarde" falsos.
    TECHO    el extremo más estricto (la prueba de que ya había llegado).
    PROMEDIO el punto medio de la ventana.

    Se toma el menor o el mayor de los dos extremos según la política, sin
    importar si el caso es entrada o salida: así no depende de la orientación
    y no hay dos fórmulas que puedan discrepar.
    """
    a, b = ventana.get("desfavor_piso"), ventana.get("desfavor_techo")
    if a is None or b is None:
        return ventana
    salida = dict(ventana)
    if politica == POLITICA_TECHO:
        elegido = max(a, b)
    elif politica == POLITICA_PROMEDIO:
        elegido = int(round((a + b) / 2.0))
    else:
        elegido = min(a, b)
    salida["aplicado"] = elegido
    return salida


def evaluar_dia(fecha, entrada_esperada, salida_esperada,
                tol_llegada_min=15, tol_salida_antes_min=5,
                entrada_piso=None, entrada_techo=None,
                salida_piso=None, salida_techo=None,
                escaneos_dia=0, escaneos_minimos=20,
                hay_turno=True, politica=POLITICA_PISO):
    """
    El veredicto completo de UN usuario en UNA fecha. Función pura: no toca la
    base de datos, y es la que se prueba entera.

    `entrada_piso`/`entrada_techo` acotan la llegada; `salida_piso`/`salida_techo`
    acotan la salida. Si solo hay un extremo (evento viejo, sin evidencia
    completa) se usa ese y se marca `evidencia_incompleta` para que la vista lo
    diga en vez de fingir precisión.
    """
    resultado = {
        "fecha": fecha,
        "estado": ESTADO_CALCULADA,
        "entrada_esperada": limpiar_hora(entrada_esperada),
        "salida_esperada": limpiar_hora(salida_esperada),
        "entrada_piso": limpiar_hora(entrada_piso),
        "entrada_techo": limpiar_hora(entrada_techo),
        "salida_piso": limpiar_hora(salida_piso),
        "salida_techo": limpiar_hora(salida_techo),
        "minutos_tarde": None,
        "minutos_antes": None,
        "minutos_indeterminados": None,
        "ventana_min": None,
        "entrada_tardia": False,
        "salida_temprana": False,
        "indeterminado": False,
        "ausente": False,
        "evidencia_incompleta": False,
        "escaneos_dia": escaneos_dia,
        "observaciones": "",
    }

    # 1) ¿Ese día había turno? Sin horario no se puede llegar tarde ni
    #    "estarse ausente": el día no aplica. Antes esto caía en AUSENTE, que
    #    es un falso positivo con nombre propio (sábado sin turno = "faltó").
    if not hay_turno or resultado["entrada_esperada"] is None \
            or resultado["salida_esperada"] is None:
        resultado["estado"] = ESTADO_NO_APLICA
        resultado["observaciones"] = "no hay horario configurado para este día"
        return resultado

    # 2) ¿Hubo escaneos? Si el sistema no escaneó no hay evidencia de nada.
    #    Este es el falso positivo más caro que había: con el escáner caído,
    #    `entrada_real is None` marcaba AUSENTE a todo el planta.
    if escaneos_dia < escaneos_minimos:
        resultado["estado"] = ESTADO_SIN_DATOS
        resultado["observaciones"] = (
            f"solo {escaneos_dia} escaneos ese día (mínimo {escaneos_minimos}): "
            "el escáner no cubrió la jornada, no se puede afirmar nada")
        return resultado

    # 3) ¿Hay presencia? Si no, y el escáner sí estuvo activo, es ausencia real.
    if resultado["entrada_piso"] is None and resultado["entrada_techo"] is None:
        resultado["estado"] = ESTADO_AUSENTE
        resultado["ausente"] = True
        resultado["observaciones"] = (
            "el escáner cubrió el día y no se detectó el equipo dentro de la "
            "ventana del turno")
        return resultado

    # 4) Llegada. Evento viejo sin `UltimaVezVisto`: se usa el techo como punto
    #    y se dice que la evidencia está incompleta, en vez de inventar precisión.
    if resultado["entrada_piso"] is None:
        resultado["entrada_piso"] = resultado["entrada_techo"]
        resultado["evidencia_incompleta"] = True

    llegada = aplicar_politica(
        clasificar_ventana(resultado["entrada_piso"], resultado["entrada_techo"],
                           resultado["entrada_esperada"], tol_llegada_min,
                           desfavorable=1),
        politica)

    if llegada["veredicto"] == VEREDICTO_TARDE:
        resultado["entrada_tardia"] = True
        resultado["minutos_tarde"] = llegada["aplicado"]
    elif llegada["veredicto"] == VEREDICTO_INDETERMINADO:
        resultado["indeterminado"] = True
        resultado["minutos_indeterminados"] = llegada["aplicado"]

    resultado["ventana_min"] = llegada.get("ancho_min")

    # 5) Salida. `desfavorable=-1`: aquí lo malo es irse ANTES de la esperada,
    #    o sea que la misma fórmula cuenta hacia el otro lado.
    if resultado["salida_piso"] is not None and resultado["salida_techo"] is not None:
        if resultado["salida_piso"] is None or resultado["salida_techo"] is None:
            resultado["evidencia_incompleta"] = True
        salida = aplicar_politica(
            clasificar_ventana(resultado["salida_piso"], resultado["salida_techo"],
                               resultado["salida_esperada"], tol_salida_antes_min,
                               desfavorable=-1),
            politica)
        if salida["veredicto"] == VEREDICTO_TARDE:
            resultado["salida_temprana"] = True
            resultado["minutos_antes"] = salida["aplicado"]
        elif salida["veredicto"] == VEREDICTO_INDETERMINADO:
            resultado["indeterminado"] = True
            resultado["minutos_indeterminados"] = salida["aplicado"]
        if resultado["ventana_min"] is None:
            resultado["ventana_min"] = salida.get("ancho_min")
    else:
        # No hay SALIDA registrada. NO es "se fue temprano" ni "estuvo": puede
        # que siga en la red (el día es de hoy) o que el equipo se apagara sin
        # evento. Decir "salida temprana" aquí sería inventar un dato.
        resultado["observaciones"] = (
            (resultado["observaciones"] + " · ") if resultado["observaciones"] else ""
        ) + "sin salida registrada (permaneció en la red o no se detectó)"

    if resultado["entrada_tardia"]:
        resultado["estado"] = ESTADO_TARDE
    elif resultado["salida_temprana"]:
        resultado["estado"] = ESTADO_SALIDA_TEMPRANA
    elif resultado["indeterminado"]:
        resultado["estado"] = ESTADO_INDETERMINADO
    else:
        resultado["estado"] = ESTADO_CALCULADA

    if resultado["evidencia_incompleta"]:
        resultado["observaciones"] = (
            (resultado["observaciones"] + " · ") if resultado["observaciones"] else ""
        ) + "evento sin evidencia de escaneo: la ventana es un punto, no un rango"

    return resultado
