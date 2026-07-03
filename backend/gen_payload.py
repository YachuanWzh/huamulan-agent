import json, random

random.seed(42)
rum_events = []

# 203 JS errors
for i in range(203):
    rum_events.append({
        'type': 'js_error',
        'name': 'TypeError',
        'value': 1.0,
        'url': '/orders/detail',
        'session_id': 'sess_%04d' % random.randint(0, 179),
        'timestamp': '2025-07-02T22:30:00+08:00',
        'metadata': {
            'message': 'Cannot read properties of undefined (reading items)',
            'stack': 'OrderDetail.render (main.js:1247)'
        }
    })

# 45 resource errors
for i in range(45):
    rum_events.append({
        'type': 'resource_error',
        'name': '/assets/order-chart.7b2c1a.js',
        'value': 1.0,
        'url': '/orders/detail',
        'session_id': 'sess_%04d' % random.randint(0, 179),
        'timestamp': '2025-07-02T22:30:00+08:00',
        'metadata': {}
    })

# LCP values - target avg ~5200, p95 ~6800
lcp_values = []
for _ in range(180):
    r = random.random()
    if r < 0.60:
        v = random.gauss(2500, 800)
    elif r < 0.90:
        v = random.gauss(6000, 1500)
    else:
        v = random.gauss(10000, 2000)
    v = max(500, v)
    lcp_values.append(round(v, 1))

lcp_values.sort()
actual_avg = sum(lcp_values)/len(lcp_values)
scale = 5200 / actual_avg
lcp_values = [round(v * scale, 1) for v in lcp_values]

for v in lcp_values:
    rum_events.append({
        'type': 'web_vital',
        'name': 'LCP',
        'value': v,
        'url': '/orders/detail',
        'session_id': 'sess_%04d' % random.randint(0, 179),
        'timestamp': '2025-07-02T22:30:00+08:00',
        'metadata': {}
    })

# Execution logs: 3 query_orders retries
execution_logs = []
for i in range(3):
    execution_logs.append({
        'id': 1000 + i,
        'created_at': '2025-07-02T22:30:00+08:00',
        'thread_id': 'thread_%04d' % i,
        'run_id': 'run_%04d' % i,
        'parent_id': None,
        'event_type': 'tool_retry',
        'status': 'retrying',
        'name': 'query_orders',
        'input': {'endpoint': '/api/orders'},
        'output': {},
        'error': {'error_type': 'TimeoutError', 'error_message': 'Request timed out after 15000ms'},
        'duration_ms': 4500,
        'token_usage': {},
        'metadata': {'tool_call_id': 'tc_%04d' % i, 'attempt': i+1, 'max_attempts': 3}
    })

payload = {'rum_events': rum_events, 'execution_logs': execution_logs}

with open('apm_payload.json', 'w', encoding='utf-8') as f:
    json.dump(payload, f)

print('Done: %d RUM events, %d exec logs' % (len(rum_events), len(execution_logs)))
print('JS errors: %d, Resource errors: %d, LCP events: %d' % (203, 45, 180))
lcp_v = [e['value'] for e in rum_events if e['type'] == 'web_vital']
lcp_v.sort()
print('LCP avg: %.1f, p95: %.1f' % (sum(lcp_v)/len(lcp_v), lcp_v[int(len(lcp_v)*0.95)-1]))
