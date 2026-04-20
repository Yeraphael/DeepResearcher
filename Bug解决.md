# DeepResearcher Bug 解决记录

## 1. `.env` 配置不生效

### 现象

- `.env` 中已配置阿里兼容接口
- 实际运行却回退到默认的 `ollama + llama3.2`

### 原因

- 后端没有自动加载 `backend/.env`

### 解决

- 在 [backend/src/config.py](F:\Project\agent\DeepResearcher\backend\src\config.py) 中增加 `load_dotenv()`

## 2. Windows 下导入搜索工具崩溃

### 现象

报错类似：

```text
UnicodeEncodeError: 'gbk' codec can't encode character ...
```

### 原因

- `SearchTool` 初始化时输出 Unicode 日志
- Windows 控制台默认 `gbk` 编码无法处理

### 解决

- 在 [backend/src/services/search.py](F:\Project\agent\DeepResearcher\backend\src\services\search.py) 中先把 `stdout/stderr` 切到 UTF-8

## 3. 前端失败时仍显示完成

### 现象

- 页面日志已经提示“研究失败”
- 顶部状态仍然显示“研究流程完成”

### 原因

- 前端原本只根据 `loading` 判断状态

### 解决

- 在 [frontend/src/App.vue](F:\Project\agent\DeepResearcher\frontend\src\App.vue) 中引入 `runState`
- 现在明确区分 `running / success / failed / cancelled`

## 4. 401 `invalid_api_key`

### 说明

- 这不是代码 bug，而是外部模型平台鉴权失败

### 当前代码改进

- 后端现在会把底层异常转换成更可读的提示

### 排查项

1. `LLM_API_KEY` 是否真实有效
2. `LLM_BASE_URL` 是否正确
3. `LLM_MODEL_ID` 是否有权限访问
4. 账号侧是否已开通模型
