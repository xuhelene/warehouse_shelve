import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--without-erp-id', action='store_true')
    parser.add_argument('--erp-id', default='10')
    parser.add_argument('--url', default='http://61.237.224.162:61888/api/open/owtbmetadata/getGcInfo')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / 'data' / 'sku.xlsx'
    sheet = openpyxl.load_workbook(source, read_only=True, data_only=True).active
    values = list(sheet.values)
    column = values[0].index('编码')
    records = [(row_number, str(row[column]).strip(), row[4])
               for row_number, row in enumerate(values[1:], 2)
               if row[column] is not None]
    endpoint = args.url
    output = root / 'results' / ('sku_remote_check_' + datetime.now(ZoneInfo('Asia/Singapore')).strftime('%Y%m%d_%H%M%S'))
    output.mkdir(parents=True, exist_ok=True)
    cache = {}
    for _, code, _ in records:
        if code in cache:
            continue
        params = {'itemCode': code} if args.without_erp_id else {'erpId': args.erp_id, 'itemCode': code}
        url = endpoint + '?' + urllib.parse.urlencode(params)
        result = {'url': url, 'checked_at': datetime.now(ZoneInfo('Asia/Singapore')).isoformat()}
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=20) as response:
                    result['http_status'] = response.status
                    body = json.loads(response.read().decode('utf-8-sig'))
                result['response'] = body
                data = body.get('data')
                if body.get('code') != 200:
                    result['status'] = '业务失败'
                elif not isinstance(data, list):
                    result['status'] = '返回结构异常'
                else:
                    matches = [item for item in data if isinstance(item, dict) and str(item.get('itemCode', '')).strip() == code]
                    result['matches'] = matches
                    result['status'] = '已有' if matches else ('未查到' if not data else '返回编号不匹配')
                break
            except Exception as error:
                result['status'] = '查询失败'
                result['error'] = str(error)
                if attempt < 2:
                    time.sleep(1)
        cache[code] = result
        (output / 'responses.json').write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8')
        if len(cache) % 20 == 0:
            print(f'Checked {len(cache)} unique codes', flush=True)
        time.sleep(0.1)
    with (output / 'sku_check.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['源文件行号', '货品编号', '本地货品名称', '核验结果', '匹配条数', '远程货品名称', 'HTTP状态', '业务码', '说明', '查询时间', '请求地址'])
        for row_number, code, name in records:
            result = cache[code]
            matches = result.get('matches', [])
            body = result.get('response', {})
            writer.writerow([row_number, code, name, result['status'], len(matches), ' | '.join(str(item.get('itemName', '')) for item in matches), result.get('http_status'), body.get('code'), result.get('error', body.get('message')), result['checked_at'], result['url']])
    summary = {'source': str(source), 'erpId': None if args.without_erp_id else args.erp_id, 'rows': len(records), 'unique_codes': len(cache), 'counts': dict(Counter(result['status'] for result in cache.values())), 'output': str(output)}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
