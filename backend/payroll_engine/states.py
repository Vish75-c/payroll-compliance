"""Indian state names to two-letter codes, so 'Karnataka', 'KA' and 'IN-KA' all mean the same state."""

STATES = {
    "AN": "Andaman and Nicobar Islands", "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh", "AS": "Assam",
    "BR": "Bihar", "CH": "Chandigarh", "CT": "Chhattisgarh", "DN": "Dadra and Nagar Haveli and Daman and Diu",
    "DL": "Delhi", "GA": "Goa", "GJ": "Gujarat", "HR": "Haryana", "HP": "Himachal Pradesh",
    "JK": "Jammu and Kashmir", "JH": "Jharkhand", "KA": "Karnataka", "KL": "Kerala", "LA": "Ladakh",
    "LD": "Lakshadweep", "MP": "Madhya Pradesh", "MH": "Maharashtra", "MN": "Manipur", "ML": "Meghalaya",
    "MZ": "Mizoram", "NL": "Nagaland", "OR": "Odisha", "PY": "Puducherry", "PB": "Punjab", "RJ": "Rajasthan",
    "SK": "Sikkim", "TN": "Tamil Nadu", "TG": "Telangana", "TR": "Tripura", "UP": "Uttar Pradesh",
    "UT": "Uttarakhand", "WB": "West Bengal",
}
_BY_NAME = {name.lower(): code for code, name in STATES.items()}
_ALIASES = {"bangalore": "KA", "bengaluru": "KA", "mumbai": "MH", "pune": "MH", "chennai": "TN", "hyderabad": "TG",
            "new delhi": "DL", "kolkata": "WB", "orissa": "OR", "uttaranchal": "UT", "ts": "TG", "od": "OR"}


def state_code(value) -> str:
    """Return the two-letter code, or '' if the value is empty or unknown."""
    if not value:
        return ""
    text = str(value).strip()
    if text.upper().startswith("IN-"):
        text = text[3:]
    if text.upper() in STATES:
        return text.upper()
    lowered = text.lower()
    return _BY_NAME.get(lowered) or _ALIASES.get(lowered, "")
