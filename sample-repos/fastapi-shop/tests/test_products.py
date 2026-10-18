def test_list_products(client):
    response = client.get("/products")
    assert response.status_code == 200
    names = [p["name"] for p in response.json()]
    assert names[:2] == ["Notebook", "Pen"]


def test_get_product(client):
    response = client.get("/products/3")
    assert response.status_code == 200
    assert response.json()["name"] == "Backpack"


def test_get_missing_product(client):
    response = client.get("/products/999")
    assert response.status_code == 404


def test_create_product(client):
    response = client.post("/products", json={"name": "Stapler", "price": 7.5, "stock": 30})
    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "Stapler"
    assert client.get(f"/products/{created['id']}").status_code == 200


def test_create_product_requires_a_name(client):
    response = client.post("/products", json={"name": "", "price": 7.5, "stock": 30})
    assert response.status_code == 422
