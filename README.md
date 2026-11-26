# 跨厂焊工实训与开工资格

联调数据采用 [《实训与考评交换约定》](交换约定.md) 的字段和时间口径。`data/training_20261201.json` 是各参与方的合成报文快照，含未核认、互相冲突或处于变化中的来源事实。

使用 Python 3.11 标准库。执行 `python3 -m unittest discover -s tests -v` 检查交换资料、本地读取与后端业务规则。需要给本机接入方提供样本时，可运行以下只读报文服务。

```sh
python3 -m http.server 8040 --bind 127.0.0.1 --directory data
```

在同一环境读取 `http://127.0.0.1:8040/training_20261201.json` 可取得 UTF-8 JSON。JSON 的缺省字段以 `null` 表示。

## 实训资格后端

`app/` 是把分厂工时回执与考评点判断接起来的服务后端：依据签到覆盖时段、当时有效的
带教范围与组别认定可计入经历，处理跨厂工时重叠与撤正，管理考评名额与成绩，
核发可跨厂验真的资格，并在开工时检查资格与现场带教。设计细节见
[`docs/architecture.md`](docs/architecture.md)。

启动（装载报文快照后提供 JSON 接口）：

```sh
python3 -m app.server --data data/training_20261201.json --host 127.0.0.1 --port 8050
```

接口通过 `X-Actor-Role` / `X-Actor-Id` 请求头区分分厂（`factory`）、考评点
（`exam_point`）、实训中心（`centre`）、工位（`station`）与学员（`worker`），
受理类接口校验报文归属，查询类接口按角色裁剪可见范围。

也可以在进程内直接使用门面：

```python
import json
from app.service import TrainingService

service = TrainingService()
service.load_packet(json.load(open("data/training_20261201.json", encoding="utf-8")))
service.certification("W17", "ship_plate", "16:30")      # 任意时点回放资格
service.station_order_view("O12", "14:45")               # 工位开工门禁解释
service.qualification_trace("W17", "ship_plate")         # 中心审计轨迹
```

