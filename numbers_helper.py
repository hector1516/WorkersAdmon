def numero_a_letras(numero):
    try:
        numero_val = float(numero)
    except Exception:
        return ""
        
    unidades = ["", "UN", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE"]
    decenas = ["", "DIEZ", "VEINTE", "TREINTA", "CUARENTA", "CINCUENTA", "SESENTA", "SETENTA", "OCHENTA", "NOVENTA"]
    especiales = {
        11: "ONCE", 12: "DOCE", 13: "TRECE", 14: "CATORCE", 15: "QUINCE",
        16: "DIECISEIS", 17: "DIECISIETE", 18: "DIECIOCHO", 19: "DIECINUEVE",
        21: "VEINTIUNO", 22: "VEINTIDOS", 23: "VEINTITRES", 24: "VEINTICUATRO",
        25: "VEINTICINCO", 26: "VEINTISEIS", 27: "VEINTISIETE", 28: "VEINTIOCHO",
        29: "VEINTINUEVE"
    }
    centenas = ["", "CIENTO", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS", "QUINIENTOS", "SEISCIENTOS", "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS"]

    def _convertir_grupo(n):
        if n == 0:
            return ""
        if n == 100:
            return "CIEN"
        
        c = n // 100
        d = (n % 100) // 10
        u = n % 10
        
        res = centenas[c]
        resto = n % 100
        
        if resto == 0:
            return res
            
        if resto in especiales:
            res += " " + especiales[resto]
        else:
            if d > 0:
                if d == 1:
                    res += " DIEZ"
                elif d == 2:
                    res += " VEINTE"
                else:
                    res += " " + decenas[d]
                    if u > 0:
                        res += " Y " + unidades[u]
            elif u > 0:
                res += " " + unidades[u]
                
        return res.strip()

    entero = int(numero_val)
    decimal = int(round((numero_val - entero) * 100))
    if decimal >= 100:
        entero += 1
        decimal -= 100
    
    if entero == 0:
        texto = "CERO"
    else:
        millones = (entero // 1000000) % 1000
        miles = (entero // 1000) % 1000
        unidades_g = entero % 1000
        
        partes = []
        if millones > 0:
            if millones == 1:
                partes.append("UN MILLÓN")
            else:
                partes.append(f"{_convertir_grupo(millones)} MILLONES")
        if miles > 0:
            if miles == 1:
                partes.append("MIL")
            else:
                partes.append(f"{_convertir_grupo(miles)} MIL")
        if unidades_g > 0:
            partes.append(_convertir_grupo(unidades_g))
            
        texto = " ".join(partes)
        
    if texto.endswith(" UN"):
        texto = texto[:-3] + " UNO"
        
    return f"{texto} PESOS {decimal:02d}/100 M.N."
