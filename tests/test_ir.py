from rpg_translator.core.ir import TextUnit, compute_source_hash, compute_text_unit_id


def test_text_unit_defaults():
    unit = TextUnit(
        id="abc",
        engine="mv",
        file_path="www/data/Map001.json",
        locator="$.events[1].pages[0].list[3].parameters[0]",
        context="",
        source_text="こんにちは",
    )
    assert unit.status == "pending"
    assert unit.translated_text is None
    assert unit.control_code_map == {}


def test_compute_text_unit_id_deterministic_and_sensitive_to_all_parts():
    id_a = compute_text_unit_id("mv", "www/data/Map001.json", "$.list[3]")
    id_b = compute_text_unit_id("mv", "www/data/Map001.json", "$.list[3]")
    assert id_a == id_b

    id_diff_engine = compute_text_unit_id("mz", "www/data/Map001.json", "$.list[3]")
    id_diff_path = compute_text_unit_id("mv", "www/data/Map002.json", "$.list[3]")
    id_diff_locator = compute_text_unit_id("mv", "www/data/Map001.json", "$.list[4]")
    assert len({id_a, id_diff_engine, id_diff_path, id_diff_locator}) == 4


def test_compute_source_hash_deterministic_and_distinguishes_text():
    assert compute_source_hash("こんにちは") == compute_source_hash("こんにちは")
    assert compute_source_hash("こんにちは") != compute_source_hash("さようなら")
