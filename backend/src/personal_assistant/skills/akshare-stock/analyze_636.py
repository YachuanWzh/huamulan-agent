#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""000636 风华高科 分析脚本"""
import sys, os, traceback, json

# 确保用 UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adapters import AkshareAdapter
from formatter import render_output
from router import IntentObj, STOCK_OVERVIEW, KLINE_ANALYSIS, FUNDAMENTAL, MONEY_FLOW

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_636_result.txt')

def main():
    try:
        adapter = AkshareAdapter()
        results = {}

        # 1. 综合评估
        obj = IntentObj(intent=STOCK_OVERVIEW, query='分析000636', symbol='000636')
        result = adapter.stock_overview(symbol='000636')
        results['综合评估'] = render_output(obj, result)

        # 2. K线
        obj2 = IntentObj(intent=KLINE_ANALYSIS, query='K线', symbol='000636', top_n=30)
        result2 = adapter.stock_kline(symbol='000636', period='daily', top_n=30)
        results['K线分析'] = render_output(obj2, result2)

        # 3. 基本面
        obj3 = IntentObj(intent=FUNDAMENTAL, query='财务', symbol='000636', top_n=8)
        result3 = adapter.fundamental(symbol='000636', top_n=8)
        results['基本面'] = render_output(obj3, result3)

        # 4. 资金流向
        obj4 = IntentObj(intent=MONEY_FLOW, query='资金流', symbol='000636', top_n=10)
        result4 = adapter.money_flow(symbol='000636', top_n=10)
        results['资金流向'] = render_output(obj4, result4)

        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            for section, text in results.items():
                f.write(f"{'='*60}\n")
                f.write(f"  {section}\n")
                f.write(f"{'='*60}\n")
                f.write(text)
                f.write('\n\n')

        print('OK: ' + OUTPUT_FILE)
    except Exception as e:
        err_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_636_error.txt')
        with open(err_file, 'w', encoding='utf-8') as f:
            f.write(traceback.format_exc())
        print('ERROR: ' + err_file)

if __name__ == '__main__':
    main()
