# billing —— 订单计费引擎

## 现状

代码最初只有 `billing/legacy.py` 一个模块。为了赶上线，税和折扣的片段被复制到了
`billing/helpers.py` 与 `billing/report.py`，之后三份各自被人改过。

现在的问题是：**同一张订单，走不同代码路径算出来的钱不一样**，财务对账时才发现。
但没人敢删任何一份——不知道哪个调用方在用哪个。

## 对外入口

- `run_billing.py <orders.json> [out.txt]` —— 对账批处理
- `billing.compute_total(order) -> dict`
- `billing.render_invoice(order, currency) -> str`

**发票文本格式被外部对账系统依赖，任何重构都不得改变输出格式。**

## 本地测试

```bash
python3 -m pytest tests_local -q
```

`tests_local/` 只是冒烟测试，覆盖常规路径；它不覆盖边界与异常。
