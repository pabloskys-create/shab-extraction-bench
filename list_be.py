import json
d = json.load(open('data/sampling/manifest_sample.json', encoding='utf-8'))
for r in d['records']:
    if r['canton'] == 'BE':
        print(r['doc_id'], r['title'][:70])