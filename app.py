from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import os
from typing import Dict, Any

# ==========================
# ЗАГРУЗКА ДАННЫХ ИЗ CSV
# ==========================
CSV_PATH = "data.csv"

if not os.path.exists(CSV_PATH):
    raise RuntimeError(f"Файл {CSV_PATH} не найден!")

# Загружаем с индексом — ваш CSV именно такой
df_raw = pd.read_csv(CSV_PATH, index_col=0)

# Исправляем опечатку (на всякий случай)
if "list_contries" in df_raw.columns:
    df_raw = df_raw.rename(columns={"list_contries": "list_countries"})

# Проверка обязательных колонок
required_cols = ["list_countries", "product", "status", "2024_tons", "2024_mln_$"]
for col in required_cols:
    if col not in df_raw.columns:
        raise RuntimeError(f"Отсутствует колонка: {col}")

# Список недружественных стран (из ваших данных и предыдущего кода)
UNFRIENDLY_COUNTRIES = {
    'United States of America', 'Canada', 'European Union Nes', 'United Kingdom',
    'Ukraine', 'Montenegro', 'Switzerland', 'Albania', 'Andorra', 'Iceland',
    'Liechtenstein', 'Monaco', 'Norway', 'San Marino', 'North Macedonia', 'Japan',
    'Korea, Republic of', 'Australia', 'Micronesia', 'New Zealand', 'Singapore',
    'Taipei, Chinese', 'Germany', 'Denmark', 'Spain', 'Italy', 'Netherlands',
    'Belgium', 'Ireland', 'Luxembourg', 'Austria', 'Greece', 'Portugal', 'Finland',
    'Sweden', 'Poland', 'Czech Republic', 'Hungary', 'Estonia', 'Latvia', 'Lithuania',
    'Slovenia', 'Slovakia', 'Malta', 'Cyprus', 'Bulgaria', 'Romania', 'Croatia',
    'France'
}

# Исправляем статус для строк с "Nan"
def fix_status(row):
    status = row["status"]
    if pd.isna(status) or status == "Nan":
        country = row["list_countries"]
        return "unfriendly" if country in UNFRIENDLY_COUNTRIES else "friendly"
    return status

df_raw["status"] = df_raw.apply(fix_status, axis=1)

# Заполняем все числовые NaN → 0.0
numeric_cols = ["2022_tons", "2023_tons", "2024_tons", "2022_mln_$", "2023_mln_$", "2024_mln_$"]
df_raw[numeric_cols] = df_raw[numeric_cols].fillna(0.0)

# Фильтруем только реальные страны (не World, не Area Nes)
df_countries = df_raw[
    ~df_raw["list_countries"].isin(["World", "Area Nes", "European Union Nes"])
].copy()

# ==========================
# СПРАВОЧНИКИ
# ==========================
PRODUCT_MAP = {
    "парфюм": ("parfum", "330300", "3303"),
    "лифт": ("lift", "842810", "8428"),
    "банкомат": ("bank", "940360", "9403"),
}

RU_TO_EN = {ru: en for ru, (en, _, _) in PRODUCT_MAP.items()}
HS_TO_EN = {hs: en for _, (en, hs, _) in PRODUCT_MAP.items()}
EN_TO_GROUP = {en: group for _, (en, _, group) in PRODUCT_MAP.items()}

# ==========================
# ФУНКЦИИ АНАЛИТИКИ
# ==========================
def resolve_product(user_input: str) -> str:
    inp = user_input.strip()
    if not inp:
        raise HTTPException(status_code=400, detail="Запрос не может быть пустым")
    if inp in PRODUCT_MAP:
        return PRODUCT_MAP[inp][0]
    if inp.isdigit() and len(inp) in (6, 10):
        for hs, en in HS_TO_EN.items():
            if inp.startswith(hs[:6]):
                return en
    if inp in ["parfum", "lift", "bank"]:
        return inp
    raise HTTPException(
        status_code=400,
        detail="Неизвестный товар. Используйте: 'парфюм', 'лифт', 'банкомат' или HS-коды: 330300, 842810, 940360."
    )

def get_customs_data(product_en: str) -> Dict[str, Any]:
    data = df_countries[df_countries["product"] == product_en]
    if data.empty:
        raise HTTPException(status_code=404, detail=f"Нет данных по товару '{product_en}'")

    total = data["2024_tons"].sum()
    unfriendly = data[data["status"] == "unfriendly"]["2024_tons"].sum()
    unfriendly_share = unfriendly / total if total > 0 else 0.0

    china = data[data["list_countries"] == "China"]
    china_share = china["2024_tons"].sum() / total if total > 0 else 0.0

    def price_per_ton(row):
        tons = row["2024_tons"]
        usd = row["2024_mln_$"] * 1_000_000
        return usd / tons if tons > 0 else 0.0

    data = data.copy()
    data["price"] = data.apply(price_per_ton, axis=1)

    china_price = data[data["list_countries"] == "China"]["price"].mean() if not china.empty else 0.0
    other_price = data[data["list_countries"] != "China"]["price"].mean()

    # Тренд по World
    world = df_raw[(df_raw["product"] == product_en) & (df_raw["list_countries"] == "World")]
    if not world.empty:
        v2022 = float(world["2022_tons"].iloc[0])
        v2024 = float(world["2024_tons"].iloc[0])
        if v2024 > v2022 * 1.1:
            trend = "increasing"
        elif v2024 < v2022 * 0.9:
            trend = "decreasing"
        else:
            trend = "stable"
    else:
        trend = "unknown"

    return {
        "total_import": total,
        "unfriendly_share": unfriendly_share,
        "china_share": china_share,
        "china_price_avg": china_price,
        "other_price_avg": other_price,
        "import_trend": trend,
    }

def get_rosstat(product_en: str) -> Dict[str, Any]:
    """
    Заглушка для внутреннего производства.
    В реальности заменить на данные из Росстата.
    """
    return {
        "production": 0.0,
        "consumption": 100000.0,
        "satisfies_consumption": False,
        "production_trend": "decreasing"
    }

def evaluate_measures(product_en: str, customs: dict, rosstat: dict) -> dict:
    s = rosstat["satisfies_consumption"]
    pt = rosstat["production_trend"]
    it = customs["import_trend"]
    us = customs["unfriendly_share"]
    cs = customs["china_share"]
    cp = customs["china_price_avg"]
    op = customs["other_price_avg"]
    ti = customs["total_import"]
    pr = rosstat["production"]

    recommendations = []

    # === МЕРА 2: Контрсанкции (без условия satisfies_consumption) ===
    if us > 0.3 and it in ["stable", "increasing"]:
        recommendations.append({
            "measure": "Повышение ставки таможенной пошлины до уровня 35-50% в рамках национального контрсанкционного регулирования",
            "reason": f"Доля недружественных стран ({us:.1%}) > 30%, импорт не снижается."
        })

    # === МЕРА 3: Антидемпинг (без условия satisfies_consumption) ===
    if cs > 0.15 and cp < op:
        recommendations.append({
            "measure": "Инициирование антидемпингового расследования в отношении экспортёров из стран(ы) Х",
            "reason": f"Доля Китая ({cs:.1%}) > 15%, цена из Китая ({cp:,.0f} $/т) ниже средней по другим странам ({op:,.0f} $/т)."
        })

    # === МЕРА 4: Преференции (требует satisfies_consumption) ===
    if EN_TO_GROUP[product_en] == "8428" and s:
        recommendations.append({
            "measure": "Расширение или инициирование применения преференциального режима в отношении товара в рамках государственных закупок",
            "reason": "Товар входит в перечень ПП-1875 (группа 8428), внутреннее производство покрывает потребление."
        })

    # === МЕРА 5: Сертификация (требует satisfies_consumption) ===
    if EN_TO_GROUP[product_en] == "9403" and it == "increasing" and pt == "increasing" and s:
        recommendations.append({
            "measure": "Применение сертификации соответствия к импортируемому товару",
            "reason": "Товар входит в ПП-2425 (группа 9403), импорт и производство растут, внутреннее предложение достаточно."
        })

    # === МЕРА 6: Иные меры ===
    if ti > pr and pt == "decreasing" and not recommendations:
        recommendations.append({
            "measure": "Иные меры защиты рынка (запрет или квотирование импорта, промышленный сбор, неавтоматическое лицензирование)",
            "reason": f"Импорт ({ti:,.0f} т) превышает производство ({pr:,.0f} т), производство падает."
        })

    if not recommendations:
        recommendations.append({
            "measure": "Нет подходящих мер ТТП",
            "reason": "На основе текущих данных не выявлено условий для применения специальных мер."
        })

    return {
        "product": product_en,
        "metrics": {
            "unfriendly_share": round(us, 3),
            "china_share": round(cs, 3),
            "china_price_avg_usd_per_ton": round(cp, 2),
            "other_price_avg_usd_per_ton": round(op, 2),
            "import_trend": it,
            "production_trend": pt,
            "domestic_supply_sufficient": s,
            "total_import_tons": round(ti, 0),
            "domestic_production_tons": round(pr, 0)
        },
        "recommendations": recommendations
    }

# ==========================
# FASTAPI APP
# ==========================
app = FastAPI(
    title="ЕАИС Backend",
    description="Аналитика на основе реальных данных из CSV",
    version="1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", include_in_schema=False)
async def serve_frontend():
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return {"error": "Файл index.html не найден"}

class TTPRequest(BaseModel):
    query: str

@app.post("/evaluate_ttp", summary="Оценка мер ТТП")
async def evaluate_ttp(request: TTPRequest):
    try:
        product = resolve_product(request.query)
        customs = get_customs_data(product)
        rosstat = get_rosstat(product)
        result = evaluate_measures(product, customs, rosstat)
        return result
    except HTTPException:
        raise
    except Exception as e:
        # Гарантируем JSON даже при неожиданной ошибке
        return {
            "error": "Внутренняя ошибка сервера",
            "detail": str(e)
        }