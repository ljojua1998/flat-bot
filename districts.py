"""Tbilisi districts (raions) and neighbourhoods shared by myhome.ge and ss.ge.

The two sites use almost the same Georgian names; ALIASES folds the few
differences onto one canonical name.
"""
RAIONS = {
    "ვაკე-საბურთალო": [
        "ვაკე", "საბურთალო", "ვაშლიჯვარი", "ვეძისი", "ნუცუბიძის ფერდობი",
        "ლისის ტბა", "ბაგები", "დიდი დიღომი", "დიღომი 1-9", "სოფ. დიღომი",
        "ვაჟა ფშაველას კვარტლები",
    ],
    "დიდუბე-ჩუღურეთი": ["დიდუბე", "ჩუღურეთი", "დიღმის მასივი", "კუკია", "სვანეთის უბანი"],
    "ძველი თბილისი": [
        "ვერა", "მთაწმინდა", "სოლოლაკი", "ავლაბარი", "აბანოთუბანი",
        "კრწანისი", "ელია", "ორთაჭალა",
    ],
    "ისანი-სამგორი": [
        "ისანი", "სამგორი", "ვარკეთილი", "ვაზისუბანი", "ორხევი", "ლილო",
        "ნავთლუღი", "მოსკოვის გამზირი", "მესამე მასივი", "აეროპორტის დას.",
        "აეროპორტის გზატ.", "ფონიჭალა",
    ],
    "გლდანი-ნაძალადევი": [
        "გლდანი", "ნაძალადევი", "მუხიანი", "სანზონა", "ავჭალა", "თემქა",
        "ზაჰესი", "გლდანულა", "კონიაკის დას.", "თბილისის ზღვა",
    ],
    "შემოგარენი": ["ოქროყანა", "წყნეთი", "კოჯორი", "ტაბახმელა", "შინდისი"],
}

ALIASES = {
    "სან. ზონა": "სანზონა",
    "ლისი": "ლისის ტბა",
}

RAION_NAMES = list(RAIONS)
URBANS = [u for r in RAIONS.values() for u in r]
URBAN_INDEX = {u: i for i, u in enumerate(URBANS)}


def canonical(name):
    if not name:
        return None
    name = name.strip()
    return ALIASES.get(name, name)


def is_known(name):
    return canonical(name) in URBAN_INDEX
