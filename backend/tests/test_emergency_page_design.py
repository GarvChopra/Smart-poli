import emergency_page as ep


def test_contact_split_and_tel_link():
    assert ep._split_contact("Suresh (son), +91 98765 43210") == ("Suresh (son)", "+919876543210")
    assert ep._split_contact("no number") == ("no number", None)


def test_schedule_in_plain_words_and_dedupe():
    assert ep._when_words("1-0-1", "after") == "Morning & night, after food"
    html = ep._medicine_rows([
        {"name": "Metformin", "dose_amount": 500, "dose_unit": "mg", "schedule_code": "BD", "food": "after"},
        {"name": "Metformin", "dose_amount": 500, "dose_unit": "mg", "schedule_code": "BD", "food": "after"},
    ])
    assert html.count("Metformin") == 1 and "1-0-1" not in html
