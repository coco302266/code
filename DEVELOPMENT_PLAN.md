# AI Classroom 三周开发计划

## 0. 总体开发策略

本项目采用：

> 开源项目运行 → 代码理解 → 功能复用 → 增量开发 → 阶段验收

而不是从零开发。

核心路线：

```text
Tutor-Chatbot
    ↓
运行原项目
    ↓
理解 FastAPI / Agent / RAG
    ↓
计算机八股知识库
    ↓
MySQL
    ↓
Student / Teacher / Classroom
    ↓
StudentProfile
    ↓
个性化 Prompt
    ↓
Teacher Resource Agent
    ↓
前端联调
    ↓
测试与报告
```

---

## 第一阶段：开源项目接管

### T0.1 项目检查

目标：

> 了解当前开源项目，不修改业务代码。

任务：

- 检查目录
- 检查 README
- 检查依赖
- 检查 Python 版本
- 检查 FastAPI
- 检查 Agent
- 检查 RAG
- 检查 ChromaDB
- 检查 LLM 配置
- 检查测试

验收：

```text
[ ] 项目结构明确
[ ] 启动方式明确
[ ] LLM 配置明确
[ ] Chat 调用链明确
[ ] Agent 调用链明确
[ ] RAG 调用链明确
```

---

### T0.2 运行原项目

目标：

> 原项目能够在本地运行。

验收：

```text
[ ] Python 环境正常
[ ] 依赖安装成功
[ ] FastAPI 启动
[ ] Swagger 可访问
[ ] Chat 可使用
[ ] RAG 可使用
```

Git：

```bash
git add .
git commit -m "chore: import and verify base tutor project"
```

---

## 第二阶段：理解核心代码

### T1.1 FastAPI

理解：

```text
main/app
 ↓
router
 ↓
service
 ↓
response
```

重点：

- API 路由
- Request
- Response
- Service

---

### T1.2 Agent

理解：

```text
graph
state
node
edge
```

重点：

- Agent 输入
- Agent 输出
- Agent State
- Agent 调度

---

### T1.3 RAG

理解：

```text
Document
 ↓
Chunk
 ↓
Embedding
 ↓
Vector DB
 ↓
Retriever
 ↓
Context
 ↓
LLM
```

验收：

能够自己解释：

> “学生的一句话是怎么最终变成 AI 回答的。”

---

## 第三阶段：计算机八股知识库

### T2.1 建立知识目录

第一版：

```text
knowledge/
└── java/
    ├── basics/
    ├── collection/
    ├── jvm/
    └── concurrent/
```

---

### T2.2 准备知识文档

第一版重点：

```text
HashMap
ArrayList
ConcurrentHashMap
JVM
GC
synchronized
volatile
```

数量优先少而精。

---

### T2.3 RAG 导入

实现：

```text
Markdown
 ↓
Chunk
 ↓
Embedding
 ↓
ChromaDB
```

metadata：

```text
subject
category
topic
source
```

---

### T2.4 RAG 验收

测试：

```text
HashMap为什么线程不安全？

JVM有哪些内存区域？

synchronized和volatile有什么区别？
```

检查检索结果是否与问题相关。

Git：

```bash
git add .
git commit -m "feat: add computer science interview knowledge base"
```

---

## 第四阶段：MySQL

### T3.1 数据库连接

配置：

```text
MySQL
SQLAlchemy
Environment Variables
```

---

### T3.2 User

字段：

```text
id
username
role
created_at
```

---

### T3.3 Classroom

字段：

```text
id
name
subject
description
created_at
```

---

### T3.4 ClassroomMember

字段：

```text
id
classroom_id
user_id
role
```

---

### T3.5 Message

字段：

```text
id
classroom_id
user_id
role
content
created_at
```

---

### T3.6 验收

必须能够：

```text
创建用户
创建课堂
加入课堂
发送消息
查询聊天记录
```

Git：

```bash
git add .
git commit -m "feat: add mysql persistence"
```

---

## 第五阶段：Student / Teacher / Classroom

### T4.1 Student

学生：

```text
User.role = student
```

---

### T4.2 Teacher

教师：

```text
User.role = teacher
```

---

### T4.3 Classroom

支持：

```text
Teacher
Student
AI
```

---

### T4.4 Chat

请求：

```json
{
  "classroom_id": 1,
  "user_id": 100,
  "message": "HashMap为什么线程不安全？"
}
```

后端必须知道：

```text
谁
在哪个课堂
问了什么
```

---

### T4.5 验收

```text
[ ] 教师可以进入课堂
[ ] 学生可以加入课堂
[ ] 学生可以聊天
[ ] 聊天记录与课堂绑定
[ ] 聊天记录与用户绑定
```

Git：

```bash
git add .
git commit -m "feat: add classroom and user roles"
```

---

## 第六阶段：StudentProfile

### T5.1 创建画像

字段：

```text
student_id
overall_level
learning_preference
knowledge_mastery
weak_points
```

---

### T5.2 Profile API

```text
GET /students/{id}/profile
PUT /students/{id}/profile
```

---

### T5.3 学习行为

记录：

```text
ask_question
repeat_question
request_explanation
```

---

### T5.4 验收

可以创建：

### Student A

```text
level = beginner
HashMap = 0.3
JVM = 0.2
```

### Student B

```text
level = advanced
HashMap = 0.8
JVM = 0.9
```

Git：

```bash
git add .
git commit -m "feat: add student profile"
```

---

## 第七阶段：个性化 Prompt

### T6.1 获取 Profile

流程：

```text
student_id
 ↓
StudentProfile
```

---

### T6.2 RAG

```text
question
 ↓
Retriever
 ↓
Context
```

---

### T6.3 Prompt

组合：

```text
Profile
+
RAG
+
Question
```

---

### T6.4 个性化回答

测试同一个问题：

```text
HashMap为什么线程不安全？
```

分别使用：

```text
Beginner
Advanced
```

比较：

```text
解释深度
术语
示例
追问
```

---

### T6.5 验收

满足：

```text
[ ] AI能够读取Profile
[ ] AI能够读取RAG
[ ] Prompt能够组合二者
[ ] Beginner回答更基础
[ ] Advanced回答更深入
```

Git：

```bash
git add .
git commit -m "feat: implement personalized teaching prompt"
```

---

## 第八阶段：Teacher Resource Agent

### T7.1 Agent

创建：

```text
Teacher Resource Agent
```

---

### T7.2 输入

```text
teacher_id
classroom_id
topic
```

---

### T7.3 RAG

获取：

```text
topic
 ↓
相关知识
```

---

### T7.4 输出

```json
{
  "title": "...",
  "objectives": [],
  "sections": [],
  "key_points": [],
  "examples": [],
  "questions": [],
  "summary": []
}
```

---

### T7.5 验收

输入：

```text
HashMap
```

能够得到合理的教学 PPT 大纲。

Git：

```bash
git add .
git commit -m "feat: add teacher resource agent"
```

---

## 第九阶段：前后端联调

### T8.1 学生流程

```text
学生进入课堂
 ↓
发送问题
 ↓
POST /chat
 ↓
AI回答
 ↓
显示在聊天室
```

---

### T8.2 教师流程

```text
教师进入课堂
 ↓
输入教学主题
 ↓
POST /resources/ppt-outline
 ↓
AI生成大纲
 ↓
前端展示
```

---

### T8.3 联调问题

重点检查：

```text
CORS
JSON格式
字段名称
错误码
API路径
超时
LLM调用失败
```

---

### T8.4 验收

```text
[ ] 学生聊天正常
[ ] AI回答正常
[ ] RAG正常
[ ] Profile生效
[ ] 教师资源生成正常
```

Git：

```bash
git add .
git commit -m "feat: integrate frontend classroom chat"
```

---

## 第十阶段：测试

### T9.1 API测试

使用 Postman。

测试：

```text
Health
Classroom
Student
Chat
Profile
Resource
```

---

### T9.2 RAG测试

至少测试：

```text
HashMap
JVM
ConcurrentHashMap
```

记录：

```text
问题
检索结果
相关性
回答
```

---

### T9.3 Profile测试

测试：

```text
Beginner
Advanced
```

相同问题的回答差异。

---

### T9.4 Agent测试

测试：

```text
Student → Teaching Agent

Teacher → Resource Agent
```

---

## 第十一阶段：性能和质量记录

记录：

```text
API响应时间
LLM响应时间
RAG检索耗时
总回答耗时
```

AI回答质量可以人工评估：

```text
准确性
相关性
完整性
个性化程度
```

不要求第一版实现复杂自动评测系统。

---

## 第十二阶段：项目整理

### T10.1 README

必须说明：

- 项目介绍
- 技术栈
- 系统架构
- 快速启动
- 环境变量
- API
- RAG
- StudentProfile
- Agent
- 项目截图

---

### T10.2 API文档

FastAPI Swagger + Markdown。

---

### T10.3 架构图

至少包含：

```text
Frontend
   ↓
FastAPI
   ↓
Service
   ↓
Agent
 ┌─┴──┐
RAG Profile
 └─┬──┘
   ↓
 LLM
```

---

### T10.4 项目报告

重点描述：

1. 用户画像
2. RAG
3. Prompt
4. Agent
5. 个性化学习
6. 教师资源生成
7. 系统测试

---

### 最终 MVP 验收清单

### 后端

- [ ] FastAPI
- [ ] MySQL
- [ ] User
- [ ] Student
- [ ] Teacher
- [ ] Classroom
- [ ] Message
- [ ] StudentProfile

### AI

- [ ] LLM
- [ ] RAG
- [ ] ChromaDB
- [ ] Teaching Agent
- [ ] Teacher Resource Agent
- [ ] Personalized Prompt

### 前后端

- [ ] Student Chat
- [ ] Teacher Resource Generation
- [ ] Chat History

### 测试

- [ ] API
- [ ] RAG
- [ ] Profile
- [ ] Agent
- [ ] 基本性能记录

### 文档

- [ ] README
- [ ] API 文档
- [ ] 架构图
- [ ] 测试报告
- [ ] 项目报告

---

### 开发优先级

如果时间不足，按照以下顺序保功能：

### P0 必须完成

```text
FastAPI
MySQL
Classroom
Student
Teacher
Chat
LLM
RAG
StudentProfile
Personalized Prompt
```

### P1 应该完成

```text
Teaching Agent
Teacher Resource Agent
PPT Outline
Chat History
```

### P2 有时间再做

```text
视频推荐
自动更新 mastery
复杂学习分析
知识图谱
复杂多 Agent
真实 PPT 文件生成
```

如果进入最后一周仍有 P0 未完成：

> 立即停止 P1/P2 功能开发，优先完成 P0。

---

### Git 提交规范

每完成一个独立阶段提交一次。

示例：

```bash
git add .
git commit -m "chore: import and verify base tutor project"

git commit -m "feat: add computer science knowledge base"

git commit -m "feat: add mysql persistence"

git commit -m "feat: add classroom and user roles"

git commit -m "feat: add student profile"

git commit -m "feat: implement personalized teaching"

git commit -m "feat: add teacher resource agent"

git commit -m "feat: integrate frontend chat"

git commit -m "test: add api and rag tests"

git commit -m "docs: update project documentation"
```

每次提交后确认：

```bash
git status
```

工作区没有意外修改后再进入下一阶段。

---

### Codex 执行规则

Codex 每次只能执行当前任务。

不得一次执行多个未验收阶段。

每个任务完成后必须输出：

```text
## 完成内容

## 修改文件

## 新增文件

## 测试方法

## 测试结果

## 已知问题

## 下一步
```

只有当前任务通过验收，才能开始下一个任务。
---
