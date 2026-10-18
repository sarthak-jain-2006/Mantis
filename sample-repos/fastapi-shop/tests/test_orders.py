def test_place_order(client):
    response = client.post("/orders", json={"items": [{"product_id": 1, "quantity": 2}]})
    body = response.json()
    assert body["subtotal"] == 9.0
    assert body["total"] == 13.99  # 9.00 + 4.99 shipping


def test_order_reduces_stock(client):
    client.post("/orders", json={"items": [{"product_id": 5, "quantity": 3}]})
    assert client.get("/products/5").json()["stock"] == 5


def test_order_with_unknown_product(client):
    response = client.post("/orders", json={"items": [{"product_id": 999, "quantity": 1}]})
    assert response.status_code == 404


def test_order_more_than_stock(client):
    response = client.post("/orders", json={"items": [{"product_id": 5, "quantity": 50}]})
    assert response.status_code == 409


def test_get_order(client):
    created = client.post("/orders", json={"items": [{"product_id": 2, "quantity": 1}]}).json()
    response = client.get(f"/orders/{created['id']}")
    assert response.status_code == 200
    assert response.json()["total"] == created["total"]
