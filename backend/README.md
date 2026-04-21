# DeepResearcher Backend 启动说明

后端目录：

- `F:\Project\agent\DeepResearcher\backend`

推荐使用你的 conda 环境：

```powershell
conda activate deepresearch
```

**第一次准备**
```powershell
cd F:\Project\agent\DeepResearcher\backend
Copy-Item .env.example .env
python -m pip install -e .
```

编辑 `backend\.env`，至少补齐：

```env
SEARCH_API=duckduckgo
LLM_PROVIDER=custom
LLM_MODEL_ID=你的模型名
LLM_API_KEY=你的真实密钥
LLM_BASE_URL=你的模型服务地址
```

**启动命令**
```powershell
conda activate deepresearch
cd F:\Project\agent\DeepResearcher\backend
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

注意：

- 要在 `F:\Project\agent\DeepResearcher\backend` 目录执行
- 不要切到 `backend\src` 再启动
- 不要写成 `python -m uvicorn main:app --reload`
- 推荐固定使用 `python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload`

启动后验证：

- 健康检查：[http://localhost:8000/healthz](http://localhost:8000/healthz)
- Swagger 文档：[http://localhost:8000/docs](http://localhost:8000/docs)

**补充**
- `/research`：非流式研究接口
- `/research/stream`：流式研究接口
- LangGraph checkpoint SQLite 文件默认位置：`backend\data\langgraph_checkpoints.sqlite`
