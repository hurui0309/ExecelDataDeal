DataDeal — Excel 数据清洗入仓（Agent 驱动）
项目简介
基于 LLM Agent 驱动的 Excel 数据清洗入仓系统。扫描指定目录下的 Excel 文件（.xlsx / .xls），通过 LLM 对表格结构进行分类和解析，最终将清洗后的数据写入 MySQL 数据库的 ODS 层。

核心流程：扫描文件 → 框线预分类/LLM 分类 → 策略解析 → 字段翻译 → MySQL 入仓

环境依赖
Python
Python 3.12.x（开发环境为 3.12.9）
第三方库
pymysql>=1.1.0
pandas>=2.0.0
openpyxl>=3.1.0
xlrd>=2.0.1
pyyaml>=6.0
openai>=1.0.0
安装依赖：

pip install -r requirements.txt
MySQL
需要可访问的 MySQL 数据库，用于存储清洗后的数据
配置见 config.yaml 中 database 部分
LLM API
需要兼容 OpenAI 接口的 LLM 服务（用于表格分类、字段翻译等）
配置见 config.yaml 中 llm 部分
配置说明
所有配置集中在 config.yaml，主要包含以下部分：

配置项	说明
database	MySQL 连接信息（host/port/user/password/database/charset）
llm	LLM API 配置（api_base/api_key/model）及生成参数（temperature/max_tokens）
scan	文件扫描配置（data_dir 为数据目录，默认 ./to_fix_data；extensions 支持的文件类型；skip_prefixes 跳过的文件前缀如 ~$）
parse	解析参数（预览行数、置信度阈值、批量写入行数、字段默认类型和长度等）
logging	日志配置（级别、文件路径模板、轮转策略）
配置文件支持环境变量替换，格式为 ${VAR_NAME}。

运行方式
# 默认使用 config.yaml，扫描 ./to_fix_data 目录
python main.py

# 指定配置文件
python main.py path/to/config.yaml

# 指定配置文件 + 覆盖数据目录（方便测试）
python main.py config.yaml ./test_data
项目结构
DataDeal/
├── main.py                  # 主入口
├── config.yaml              # 配置文件
├── config_loader.py         # 配置加载模块（YAML + 环境变量替换）
├── requirements.txt         # Python 依赖
├── agents/
│   ├── orchestrator.py      # 编排 Agent：扫描、分发、协调、汇总
│   ├── classifier.py        # 分类 Agent：LLM 驱动的表格结构分类
│   └── worker.py            # 工作 Agent：根据策略执行解析和入仓
├── services/
│   ├── excel_preview.py     # Excel 预览读取（list_sheets / preview / 前 N 列）
│   ├── excel_reader.py      # Excel 数据读取（含合并单元格填充与多路径回退）
│   ├── excel_utils.py       # Excel 工具函数
│   ├── border_info.py       # 框线信息提取（用于预分类）
│   ├── llm_client.py        # LLM API 客户端封装（鉴权错误不重试）
│   ├── mysql_writer.py      # MySQL 建表 + 批量写入 + 字段自动扩宽
│   ├── name_translate.py    # 字段名翻译服务（LLM 驱动）
│   └── xlrd_patch.py        # xlrd unpack_unicode UTF-16 容错 patch（main.py 启动时 import）
├── strategies/
│   ├── __init__.py          # 策略注册表（导出 BUILTIN_DESCRIPTIONS、get_strategy）
│   ├── strategy_standard.py           # 标准表策略
│   ├── strategy_simple_header.py      # 简单单行表头策略
│   ├── strategy_multi_header.py       # 多行表头策略
│   ├── strategy_horizontal_split.py   # 水平分表策略
│   ├── strategy_vertical_subtable.py  # 纵向子表策略
│   └── strategy_paired_row_bilingual.py # 双语对照行策略
├── to_fix_data/             # 待处理数据目录（默认扫描目录，文件为扁平化命名）
├── 待清洗数据/               # 原始数据目录（嵌套目录结构）
├── test_data/               # 测试数据
├── test_single/             # 单文件测试
├── tmp_data/                # 临时数据处理目录
├── tmp_file/                # 临时文件（运行日志等）
├── logs/                    # 日志输出目录
└── .venv/                   # Python 虚拟环境（不纳入版本控制）
解析策略说明
系统支持以下表格结构分类策略，由分类器（LLM + 框线预分类）自动选择：

策略	适用场景
standard	标准表格
simple_header	单行表头的简单表格
multi_header	多行合并表头的复杂表格
horizontal_split	同一 Sheet 中水平方向有多个独立表格
vertical_subtable	纵向子表（同一列中包含多个子表）
paired_row_bilingual	中英文对照的双语表格
注意事项
xlrd 补丁：main.py 启动时通过 `import services.xlrd_patch` 应用 unpack_unicode 容错 patch，以兼容部分 VBA 保护的 .xls 文件中的非法 UTF-16 代理对（幂等，重复 import 安全）
幂等处理：系统会根据 source_path + sheet_name 去重，已成功（status=SUCCESS）解析的 Sheet 会被跳过；SKIP/ERROR/UNKNOWN 状态的记录会重试覆盖
MySQL 表名/字段名：自动截断至 64 字符（含去重后缀），字段名中的特殊字符替换为下划线
错误占位表名：ERROR/SKIP/UNKNOWN 状态使用 `<状态>_<文件>_<sheet>_s<idx>` 形式，避免同一文件多 sheet 互相覆盖日志
数据目录：to_fix_data 中的文件使用扁平化命名（将原始嵌套目录路径的 / 和空格替换为 _），原始目录结构保留在 待清洗数据 目录中
to_fix_data 与待清洗数据路径映射
to_fix_data 目录中的文件是从 待清洗数据 目录中挑选的测试文件，采用扁平化命名（目录分隔符 / 和空格均替换为 _）。以下为每个文件的对应关系：

命名规则
将 待清洗数据 中的相对路径去掉前缀 待清洗数据/待清洗数据/，把路径分隔符 / 和空格替换为 _，即得到 to_fix_data 中的文件名。

例：待清洗数据/待清洗数据/中国农村统计年鉴-Excel版（1985-2024年）/中国农村统计年鉴1985（excel）/01 农村经济形式和经营方式/主要部门的国营农林牧渔场发展情况.xlsx → 中国农村统计年鉴-Excel版（1985-2024年）_中国农村统计年鉴1985（excel）_01_农村经济形式和经营方式_主要部门的国营农林牧渔场发展情况.xlsx

文件映射表
#	to_fix_data 文件名	待清洗数据 中的相对路径（去掉前缀 待清洗数据/待清洗数据/）
1	中国农村统计年鉴-Excel版（1985-2024年）_中国农村统计年鉴1985（excel）_01_农村经济形式和经营方式_主要部门的国营农林牧渔场发展情况.xlsx	中国农村统计年鉴-Excel版（1985-2024年）/中国农村统计年鉴1985（excel）/01 农村经济形式和经营方式/主要部门的国营农林牧渔场发展情况.xlsx
2	中国农村统计年鉴-Excel版（1985-2024年）_中国农村统计年鉴1985（excel）_03_主要农产品产量_主要农产品产量与历史最高年比较(一).xlsx	中国农村统计年鉴-Excel版（1985-2024年）/中国农村统计年鉴1985（excel）/03 主要农产品产量、工业产品产量和主要农产品商品量/主要农产品产量与历史最高年比较(一).xlsx
3	中国农村统计年鉴-Excel版（1985-2024年）_中国农村统计年鉴1985（excel）_04_农产品价格和成本_主要农产品与工业品的交换比价(一).xlsx	中国农村统计年鉴-Excel版（1985-2024年）/中国农村统计年鉴1985（excel）/04 农产品价格和成本/主要农产品与工业品的交换比价(一).xlsx
4	中国农村统计年鉴-Excel版（1985-2024年）_中国农村统计年鉴1985（excel）_05_农村经济效益_农村经济效益(一).xlsx	中国农村统计年鉴-Excel版（1985-2024年）/中国农村统计年鉴1985（excel）/05 农村经济效益/农村经济效益(一).xlsx
5	中国农村统计年鉴-Excel版（1985-2024年）_中国农村统计年鉴1985（excel）_07_农民家庭收入和生活消费_农民家庭人口和平均每人纯收入.xlsx	中国农村统计年鉴-Excel版（1985-2024年）/中国农村统计年鉴1985（excel）/07 农民家庭收入和生活消费/农民家庭人口和平均每人纯收入.xlsx
6	中国农村统计年鉴-Excel版（1985-2024年）_中国农村统计年鉴1986（excel）_06_农村经济收入及分配_乡村两级企业主要财务指标.xlsx	中国农村统计年鉴-Excel版（1985-2024年）/中国农村统计年鉴1986（excel）/06 农村经济收入及分配/乡村两级企业主要财务指标.xlsx
7	中国农村统计年鉴-Excel版（1985-2024年）_中国农村统计年鉴1987（excel）_02_农村社会总产值_分部门农业物质消耗、净产值及构成.xlsx	中国农村统计年鉴-Excel版（1985-2024年）/中国农村统计年鉴1987（excel）/02 农村社会总产值/分部门农业物质消耗、净产值及构成.xlsx
8	中国农村统计年鉴-Excel版（1985-2024年）_中国农村统计年鉴1987（excel）_10_各地区主要经济指标排序与分组_水产品产量.xlsx	中国农村统计年鉴-Excel版（1985-2024年）/中国农村统计年鉴1987（excel）/10 各地区主要经济指标排序与分组/水产品产量.xlsx
9	中国农村统计年鉴-Excel版（1985-2024年）_中国农村统计年鉴1987（excel）_附录_台湾省主要农业统计资料(一).xlsx	中国农村统计年鉴-Excel版（1985-2024年）/中国农村统计年鉴1987（excel）/附录/台湾省主要农业统计资料(一).xlsx
10	中国农村统计年鉴-Excel版（1985-2024年）_中国农村统计年鉴1989（excel）_03_农村基本情况_乡村两级企业发展情况.xlsx	中国农村统计年鉴-Excel版（1985-2024年）/中国农村统计年鉴1989（excel）/03 农村基本情况/乡村两级企业发展情况.xlsx
11	中国农村统计年鉴-Excel版（1985-2024年）_中国农村统计年鉴1989（excel）_09_农民家庭收入和生活消费_农民家庭住房情况.xlsx	中国农村统计年鉴-Excel版（1985-2024年）/中国农村统计年鉴1989（excel）/09 农民家庭收入和生活消费/农民家庭住房情况.xlsx
12	中国农村统计年鉴-Excel版（1985-2024年）_中国农村统计年鉴1989（excel）_附录_台湾省主要农产品产量(一).xlsx	中国农村统计年鉴-Excel版（1985-2024年）/中国农村统计年鉴1989（excel）/附录/台湾省主要农产品产量(一).xlsx
注意：to_fix_data 中还存在 3 个 Excel 临时文件（以 ~$ 开头），这些是 Excel 编辑时自动生成的，程序运行时会自动跳过，无需复制。