# 受控聚合

先读当前服务capabilities，只有aggregation=true且transport_coverage含data.aggregate才调用。旧客户端可能没有此命令；不能因此升级全局安装或改宿主配置。优先可信MCP的aggregate_data；SDK为aggregate(identifier, revision, query, scope="own")。

1. 从实际项目列表取得ID，从schema读取**明确修订**的字段类型。查询内只引用发布字段；业务含义、单位和可加性不能由字段名猜测。未确认口径只报告技术试算，不命名为正式指标。
2. 用有限条件构造query：filters最多5组，外层AND，每组mode为and/or，总条件最多40；group_by最多5个字段，可对date字段按day/month/quarter/year分桶；measures最多8项，op为count/sum/avg/min/max，alias唯一。sum/avg仅数值；decimal条件按十进制字符串。
3. 默认scope=own；只有具备record.read.all才可显式all，不因被拒绝自动切账号。现有适配器只读取未归档记录的最新确认，按revision精确匹配，不包含工作候选。
4. 结果必须核对eligible_records、excluded_schema_records、matched_records、source_policy、schema_revision和complete。不同修订被排除时如实说明，不能将某修订结果称整个项目总计；不自动循环多修订再合并。
5. count空集为0；sum/avg/min/max空集或全NULL为null，不改成0。缺失字段按NULL；条件ne/not_in不匹配NULL，要显式is_null。日期是无时区日历日期；year为公历年，不代表客户财政期间。avg由数据库四舍五入到8位小数；数字字符串不得经二进制浮点转换。
6. max_groups默认100、最多1000。超限整次失败，没有可用的截断总计；缩小条件重新查。不拼SQL、JOIN、函数表达式、JSONPath或任意HTTP绕过。

CLI示例（query.json仅含query对象，不含凭证；使用获准安全工作路径，不提交仓库）：

```bash
office --service <已确认企业地址> aggregate <真实数据集ID> --revision 1 --file <安全路径/query.json>
```

构造格式示例，字段标识必须替换为实际Schema中的合法字段：

```json
{
  "filters": [{"mode": "and", "predicates": [{"field": "scalar_value", "op": "gte", "values": ["0.00"]}]}],
  "group_by": [{"field": "calendar_date", "bucket": "month"}],
  "measures": [{"alias": "row_count", "op": "count"}, {"alias": "value_sum", "op": "sum", "field": "scalar_value"}],
  "max_groups": 100
}
```

文字和图表只使用返回数字及覆盖事实，由客户端确定性脚本格式化/校验，再交模型表达；本接口不生成业务报表或自动发送企微。
