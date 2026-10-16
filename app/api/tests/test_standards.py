from seed_data import STANDARDS


def test_list_standards(client):
    response = client.get("/standards")
    assert response.status_code == 200
    body = response.json()
    assert [s["code"] for s in body] == sorted(s["code"] for s in STANDARDS)
    assert body[0].keys() == {"code", "domain", "subject", "description"}
