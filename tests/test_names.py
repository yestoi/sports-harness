from harness.matching.names import normalize_name


def test_normalize_examples():
    assert normalize_name("San José State") == "san jose state"
    assert normalize_name("Alabama St.") == "alabama state"
    assert normalize_name("NY Giants") == "new york giants"
    assert normalize_name("LA Rams") == "los angeles rams"
    assert normalize_name("Arkansas-Pine Bluff") == "arkansas pine bluff"
    assert normalize_name("Texas A&M") == "texas a and m"
    assert normalize_name("St. John's") == "st johns"
    assert normalize_name("  Kansas  City ") == "kansas city"
    assert normalize_name("Hawai'i Rainbow Warriors") == "hawaii rainbow warriors"
