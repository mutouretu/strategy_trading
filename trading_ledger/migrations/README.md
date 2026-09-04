# 数据库迁移

`001_initial.sql` 是统一账本的全新 Schema，`002_project_color.sql` 为项目增加可选显示颜色，`003_remove_t_plus_one.sql` 将已有持仓调整为全部可卖。迁移器会校验数据库身份并按版本顺序升级，不接收或识别原型数据库，也不执行原型数据迁移。
