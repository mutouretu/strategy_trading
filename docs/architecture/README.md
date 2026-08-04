# 当前架构文档

`current_architecture.tex` 描述第 6 部分 6B 技术验收、第四部分 4A—4D 内容锁定后的
实际软件架构，包括：

- 单体仓库中的三个并列工程及其职责；
- 包级依赖关系；
- 单次仿真与逐 K 线执行链；
- Strategy、Rule 和 Adapter 边界；
- Plugin 与 Experiment Provider；
- Study、Experiment、Metric 和 SQLite 数据流；
- COIN-M / USD-M（U 本位）隔离；
- 未来 Live Adapter 位置；
- 当前完成度、第四部分长期市场环境和 6C 的衔接点。

使用 XeLaTeX 编译：

```bash
cd docs/architecture
latexmk -xelatex -interaction=nonstopmode -halt-on-error \
  current_architecture.tex
```

建议只提交 `.tex` 和本说明；PDF 可在需要评审或归档时生成。
