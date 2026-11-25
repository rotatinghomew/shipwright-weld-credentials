# 跨厂焊工实训与开工资格

联调数据采用 [《实训与考评交换约定》](交换约定.md) 的字段和时间口径。`data/training_20261201.json` 是各参与方的合成报文快照，含未核认、互相冲突或处于变化中的来源事实。

使用 Python 3.11 标准库。执行 `python3 -m unittest discover -s tests -v` 检查交换资料和本地读取。需要给本机接入方提供样本时，可运行以下只读报文服务。

```sh
python3 -m http.server 8040 --bind 127.0.0.1 --directory data
```

在同一环境读取 `http://127.0.0.1:8040/training_20261201.json` 可取得 UTF-8 JSON。JSON 的缺省字段以 `null` 表示。
