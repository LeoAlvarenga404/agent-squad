"""Phone matching key. Pure stdlib: the Hermes guard plugin ships a copy of this file."""
import re


def phone_key(number, country_code: str = "55", area_digits: int = 2, tail_digits: int = 8) -> str | None:
    """Area code + last subscriber digits. Tolerates the country prefix, formatting and an extra leading digit
    (e.g. Brazil's 9th mobile digit)."""
    d = re.sub(r"\D", "", str(number or ""))
    local = area_digits + tail_digits
    if country_code and d.startswith(country_code) and len(d) >= len(country_code) + local:
        d = d[len(country_code):]
    elif len(d) > local + 1:
        return "+" + d  # another country's number: only an exact match counts
    return d[:area_digits] + d[-tail_digits:] if len(d) >= local else None
