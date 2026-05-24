import requests
import json

base_url = "http://127.0.0.1:8000"
s = requests.Session()

# 1. Login
res = s.post(f"{base_url}/api/admin/login", json={"username": "admin", "password": "admin888"})
print("Login:", res.json())

# 2. List contributions
res = s.get(f"{base_url}/api/contribution/list?targetType=shop")
data = res.json()
print(f"List length before delete: {len(data['posts'])}")

if len(data['posts']) > 0:
    first_id = data['posts'][0]['id']
    # 3. Delete
    print(f"Deleting {first_id}...")
    del_res = s.delete(f"{base_url}/api/admin/contribution/{first_id}")
    print("Delete result:", del_res.json())

    # 4. List again
    res = s.get(f"{base_url}/api/contribution/list?targetType=shop")
    data2 = res.json()
    print(f"List length after delete: {len(data2['posts'])}")
else:
    print("No posts found to delete.")
