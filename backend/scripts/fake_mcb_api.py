"""A local stand-in for the MCB Read-Only API, for trying the ERP column mapping without the real key or data.

    python scripts\\fake_mcb_api.py                     # http://127.0.0.1:5055/MCB-api, key: local-test-key, 300 made-up students
    set ERP_API_URL=http://127.0.0.1:5055/MCB-api  and  ERP_API_KEY=local-test-key  for the backend, then use
    Settings -> ERP import -> API connection.

Same contract as the real API: GET /views, /views/{view}/schema, /views/{view}?skip&limit(<=500)&sort_by&<column>=<value>,
/health/db (open); the key in the x-api-key header. All names and numbers are generated, not real people.
"""
import os
import sys

from flask import Flask, abort, jsonify, request

KEY = os.getenv('FAKE_MCB_KEY', 'local-test-key')
N = int(os.getenv('FAKE_MCB_ROWS', '300'))
VIEW = 'vw_StudentDetails'
BRANCHES = [('148', 'MS Creative School Mira Road', 'MRR'), ('62', 'MS Creative School Malakpet Girls', 'MPG'), ('211', 'MS Creative Kids Mira Road', 'MKR')]
ROWS = [{'StudentEnrollmentCode': f'26{BRANCHES[i % 3][2]}{i:04d}', 'FullName': f'Sample Student {i}', 'ClassName': str(i % 10 + 1),
         'Section': 'AB'[i % 2], 'Gender': 'Male' if i % 2 else 'Female', 'FatherPhone': f'98{i:08d}', 'Status': 'Active' if i % 17 else 'Inactive',
         'BranchID': BRANCHES[i % 3][0], 'BranchName': BRANCHES[i % 3][1], 'AcademicYear': '2026-2027'} for i in range(N)]

app = Flask(__name__)


def need_key():
    if request.headers.get('x-api-key') != KEY:
        abort(401)


@app.get('/MCB-api/')
def root():
    return jsonify({'status': 'ok', 'message': 'Fake ERP Read-Only API is running.'})


@app.get('/MCB-api/health/db')
def health():
    return jsonify({'database': 'connected'})


@app.get('/MCB-api/views')
def views():
    need_key()
    return jsonify([VIEW, 'vw_Fees'])


@app.get('/MCB-api/views/<view>/schema')
def schema(view):
    need_key()
    if view != VIEW:
        abort(404)
    return jsonify([{'column_name': c, 'data_type': 'varchar'} for c in ROWS[0]])


@app.get('/MCB-api/views/<view>')
def rows(view):
    need_key()
    if view != VIEW:
        abort(404)
    a = request.args.to_dict()
    skip, limit = int(a.pop('skip', 0)), min(int(a.pop('limit', 50)), 500)
    sort_by, sort_dir = a.pop('sort_by', None), a.pop('sort_dir', 'asc')
    data = [r for r in ROWS if all(str(r.get(k)) == v for k, v in a.items())]
    if sort_by:
        data.sort(key=lambda r: r.get(sort_by) or '', reverse=sort_dir == 'desc')
    return jsonify(data[skip:skip + limit])


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5055
    app.run(host='127.0.0.1', port=port)
