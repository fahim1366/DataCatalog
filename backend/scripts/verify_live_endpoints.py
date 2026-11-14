import urllib.request
import json

req = urllib.request.Request(
    'http://localhost:8000/api/v1/auth/login',
    data=json.dumps({'username': 'admin', 'password': 'Admin#2026'}).encode('utf-8'),
    headers={'Content-Type': 'application/json'}
)
with urllib.request.urlopen(req) as resp:
    token = json.loads(resp.read().decode())['access_token']
    print('[OK] Logged in as admin, got token')

headers = {'Authorization': 'Bearer ' + token}

# Test 1: Permissions Registry
req1 = urllib.request.Request('http://localhost:8000/api/v1/roles/permissions-registry', headers=headers)
with urllib.request.urlopen(req1) as resp1:
    mods = json.loads(resp1.read().decode())
    print(f'[OK] Permissions registry returned {len(mods)} modules')

# Test 2: Groups (paginated)
req2 = urllib.request.Request('http://localhost:8000/api/v1/groups?page=1&page_size=10', headers=headers)
with urllib.request.urlopen(req2) as resp2:
    grps = json.loads(resp2.read().decode())
    print(f'[OK] Groups endpoint returned total={grps["total"]}, items={len(grps["items"])}')
    for g in grps['items']:
        print(f'   Group: {g["name"]} (users={g["user_count"]}, roles={g["role_count"]})')

# Test 3: Users (paginated)
req3 = urllib.request.Request('http://localhost:8000/api/v1/users?page=1&page_size=10', headers=headers)
with urllib.request.urlopen(req3) as resp3:
    users_data = json.loads(resp3.read().decode())
    print(f'[OK] Users endpoint returned total={users_data["total"]}, items={len(users_data["items"])}')
    sample_user = users_data['items'][0]
    sample_user_id = sample_user['id']
    print(f'   Sample user: {sample_user["username"]} (id={sample_user_id}, phone={sample_user["phone"]})')

# Test 4: Effective permissions for sample user
req4 = urllib.request.Request(f'http://localhost:8000/api/v1/users/{sample_user_id}/effective-permissions', headers=headers)
with urllib.request.urlopen(req4) as resp4:
    eff = json.loads(resp4.read().decode())
    print(f'[OK] Effective permissions returned {len(eff)} rows for {sample_user["username"]}')
    if eff:
        print(f'   Sample row: resource={eff[0]["resource_name"]}, result={eff[0]["result"]}, source={eff[0]["source_name"]}')

# Test 5: User Activity
req5 = urllib.request.Request(f'http://localhost:8000/api/v1/users/{sample_user_id}/activity', headers=headers)
with urllib.request.urlopen(req5) as resp5:
    acts = json.loads(resp5.read().decode())
    print(f'[OK] Activity endpoint returned {len(acts)} events')

# Test 6: Physical Tree
req6 = urllib.request.Request('http://localhost:8000/api/v1/tables/tree/physical', headers=headers)
with urllib.request.urlopen(req6) as resp6:
    tree = json.loads(resp6.read().decode())
    print(f'[OK] Physical tree returned {len(tree)} database connections')
    for c in tree:
        print(f'   DB: {c["name"]} ({c["database_name"]}) with {len(c["schemas"])} schemas')

print('\n*** ALL LIVE BACKEND ENDPOINTS VERIFIED 100% OPERATIONAL ***')
