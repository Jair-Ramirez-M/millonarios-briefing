# Briefing Millonarios FC

Sistema de monitoreo que arma un **briefing diario** sobre Millonarios FC
(equipo masculino) a partir de canales de YouTube y prensa, y mantiene un motor
de credibilidad — **"Quién es Quién"** — que registra qué fuente acierta, cuál
mete "humo" y quién dio cada información primero.

Funciona a **costo $0**: se ejecuta solo en GitHub Actions (cron gratuito) y se
publica como página estática en GitHub Pages.

---

## Qué hace

- **Recolecta** dos veces al día: 12 canales de YouTube (vía RSS gratuito) y el
  agregador de prensa Tineus (El Espectador, Colombia.com, Primer Tiempo, etc.).
- **Analiza** las publicaciones: redacta el resumen del día y separa lo
  confirmado de lo especulativo. Usa la capa **gratuita** de Google Gemini; si
  no hay clave, sigue funcionando en modo básico (sin IA).
- **Califica fuentes**: cada afirmación de mercado (llegada, salida, renovación)
  se agrupa por rumor; cuando el rumor se resuelve, se reparte acierto / fallo /
  humo y se actualiza el historial de cada fuente.
- **Publica** un tablero web con tres secciones: El Briefing, Radar de Rumores y
  Quién es Quién.

---

## Puesta en marcha (3 pasos)

### 1. Subir a GitHub

Crea un repositorio nuevo (puede ser privado) y sube todo el contenido de esta
carpeta. Por la web: *Add file → Upload files*, arrastra todo y confirma.

### 2. Activar el tablero (GitHub Pages)

En el repo: **Settings → Pages**. En *Source* elige **Deploy from a branch**,
rama `main` y carpeta **`/docs`**. Guarda. En uno o dos minutos el tablero
quedará en `https://TU-USUARIO.github.io/TU-REPO/`.

### 3. Agregar la clave de Gemini (gratis y opcional)

El sistema funciona sin esto, pero la clave activa el análisis con IA (mejores
resúmenes y detección de rumores).

1. Entra a **Google AI Studio** (`aistudio.google.com`) y genera una API key
   gratuita.
2. En el repo: **Settings → Secrets and variables → Actions → New repository
   secret**.
3. Nombre: `GEMINI_API_KEY` — Valor: tu clave. Guarda.

> La clave vive solo como *secret* de GitHub. Nunca va en el código.

**Listo.** El briefing se generará solo a las 6:30 a.m. y 6:30 p.m. (hora
Colombia). Para dispararlo de inmediato: pestaña **Actions → Briefing
Millonarios → Run workflow**.

---

## Uso bajo demanda (en tu computador)

Requiere Python 3.10+.

```bash
pip install -r requirements.txt

python run.py            # ejecución completa: recolecta, analiza y arma el briefing
python run.py collect    # solo recolecta (sin IA)
python run.py rumors     # lista los rumores en seguimiento y su "tema" (slug)
```

Para usar la IA localmente, define la clave antes de correr:

```bash
export GEMINI_API_KEY="tu-clave"     # Windows:  set GEMINI_API_KEY=tu-clave
python run.py
```

Abre `docs/index.html` en el navegador para ver el resultado.

---

## El motor de credibilidad

Una afirmación solo se puede **calificar cuando se resuelve**. El sistema agrupa
las afirmaciones por rumor y espera el desenlace.

- **Automático:** si el canal oficial del club confirma algo, ese rumor queda
  resuelto como *confirmado* sin que hagas nada.
- **Manual:** para los demás, tú marcas el desenlace:

```bash
python run.py rumors                              # ver los slugs disponibles
python run.py resolve llegada-extremo confirmado  # confirmado | desmentido | humo
```

Al resolver, cada fuente que opinó sobre ese rumor recibe su acierto, fallo o
humo, y el ranking se recalcula.

**Sé paciente con esta parte.** El ranking nace vacío y madura con los datos:
una fuente necesita varios rumores ya resueltos antes de tener un nivel de
confianza fiable (hasta entonces aparece como *"Sin datos"*). Cuantos más
desenlaces marques, más certero se vuelve.

---

## Editar las fuentes

Todo está en `config/sources.yaml`. Para sumar un canal de YouTube basta con
pegar su `@handle`. El campo `type`/`weight` es solo la confianza inicial — el
motor aprende la real con el tiempo.

Ajustes generales (zona horaria, ventana del briefing, filtros, modelo de
Gemini) en `config/settings.yaml`.

---

## Estructura

```
config/      sources.yaml (fuentes) · settings.yaml (ajustes)
src/         collectors · pipeline · analyze · credibility · briefing
data/        items.json (historial) · ledger.json (motor de credibilidad)
docs/        index.html (tablero) · data/briefing.json (lo que muestra)
run.py       orquestador
.github/     workflow del cron
```

---

## Notas honestas

- **X / Twitter** no está incluido: a $0 su API ya no es viable. El sistema está
  pensado para sumar luego un proveedor externo (~USD 20–30/mes) si lo quieres.
- El raspado de **Tineus** depende de la estructura de esa página; si algún día
  cambia su diseño, el recolector de Tineus podría necesitar un ajuste menor.
- Los nombres de modelos de Gemini cambian con el tiempo; si el análisis con IA
  empieza a fallar, actualiza `gemini_model` en `config/settings.yaml`.
