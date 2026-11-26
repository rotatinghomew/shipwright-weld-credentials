"""跨厂实训与开工资格服务（标准库实现）。

模块分工：

- ``timeutil`` / ``segments``：值班日时间口径与时段运算
- ``models``：交换报文对应的领域事实与运行期记录
- ``experience``：实操经历认定（带教范围、跨厂重叠复核）
- ``seating``：考评名额状态机（hold / confirm / move、期限与容量）
- ``certification``：资格条件汇总与暂停逻辑
- ``orders``：开工门禁与带教认定
- ``service``：面向分厂、考评点、中心、工位的统一服务门面
- ``server``：基于 ``http.server`` 的 JSON 接口
"""

__all__ = ["service", "server"]
