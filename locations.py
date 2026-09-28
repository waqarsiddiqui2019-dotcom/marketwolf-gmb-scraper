# Pincode-by-pincode coverage instead of neighborhood names - exhaustively
# works through every pincode in a city (ascending, starting 400001 in
# Mumbai) before moving to the next city, so "all doctors in this area"
# means "all doctors in this pincode" rather than a rougher named locality.
#
# Mumbai uses the FULL sequential 400001-400104 range rather than a
# hand-picked "valid" subset - an earlier curated list (built from a postal
# lookup search) turned out to be missing real, assigned codes (400083,
# 400086, etc), so completeness now wins over trimming a handful of
# unassigned numbers. A pincode with no post office just returns few/no
# Google Maps results - harmless, not an error.
#
# Navi Mumbai/Thane are the codes for the same localities the original
# neighborhood-based version covered. Kalyan-Dombivli (421xxx prefix, a
# separate municipal corporation from Thane) is its own city group appended
# after Thane.

PINCODES = {
    "Mumbai": [f"4000{n:02d}" if n < 100 else f"400{n}" for n in range(1, 105)],
    "Navi Mumbai": [
        "400614", "400703", "400706", "400708", "400710", "410206", "410209",
        "410210",
    ],
    "Thane": [
        "400601", "400602", "400604", "400605", "400606", "400607", "400612",
        "400615",
    ],
    "Kalyan-Dombivli": [
        "421201", "421202", "421203", "421204", "421301", "421304", "421306",
    ],
}

# Mostly specialized doctors. Every pincode is searched once per specialty
# below, in this order.
SPECIALTY_SEARCH_TERMS = [
    "cardiologist", "dermatologist", "orthopedic doctor", "gynecologist",
    "pediatrician", "ent specialist", "dentist", "psychiatrist",
    "urologist", "diabetologist", "gastroenterologist", "neurologist",
    "ophthalmologist", "nephrologist", "pulmonologist", "oncologist",
    "endocrinologist", "rheumatologist", "sexologist", "psychologist",
    "physiotherapist", "homeopathy doctor", "ayurvedic doctor",
    "dietitian nutritionist", "cosmetic surgeon", "general surgeon",
    "ivf specialist", "proctologist", "general physician",
]
GENERAL_FALLBACK_TERM = "doctors"

# KPI experiment: 1 in 5 pincodes (20%) also gets a generic "doctors" search
# after its specialty searches, to test whether the broad catch-all turns up
# leads the specific specialty searches miss (e.g. small multi-specialty or
# unlabeled clinics). The other 4 in 5 stay specialty-only. If the generic
# search's results prove worthwhile, this ratio can be raised later.
GENERAL_SEARCH_EVERY_NTH_PINCODE = 5


def build_queries(search_term):
    """Yields (city, pincode, query_string) tuples for every pincode, one city at a time."""
    for city, pincodes in PINCODES.items():
        for pincode in pincodes:
            query = f"{search_term} near {pincode} {city}"
            yield city, pincode, query


def build_priority_queries():
    """
    Yields (city, pincode, specialty_term, query_string) for the FULL daily
    grid: for each pincode, every specialty term (in SPECIALTY_SEARCH_TERMS
    order) is queried, then - for 1 in every GENERAL_SEARCH_EVERY_NTH_PINCODE
    pincodes (20%) - one extra generic "doctors" search for that same
    pincode, as a running KPI test of whether it's worth keeping.
    """
    pincode_counter = 0
    for city, pincodes in PINCODES.items():
        for pincode in pincodes:
            for term in SPECIALTY_SEARCH_TERMS:
                query = f"{term} near {pincode} {city}"
                yield city, pincode, term, query

            if pincode_counter % GENERAL_SEARCH_EVERY_NTH_PINCODE == 0:
                query = f"{GENERAL_FALLBACK_TERM} near {pincode} {city}"
                yield city, pincode, GENERAL_FALLBACK_TERM, query

            pincode_counter += 1
