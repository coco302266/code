-- MySQL dump 10.13  Distrib 8.0.41, for Win64 (x86_64)
--
-- Host: 127.0.0.1    Database: ai_classroom
-- ------------------------------------------------------
-- Server version	8.0.41

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Table structure for table `agent_runs`
--

DROP TABLE IF EXISTS `agent_runs`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `agent_runs` (
  `id` int NOT NULL AUTO_INCREMENT,
  `classroom_id` int DEFAULT NULL,
  `user_id` int DEFAULT NULL,
  `scenario` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `topic` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `roles` json NOT NULL,
  `degraded` tinyint(1) NOT NULL,
  `reason` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `duration_ms` int NOT NULL,
  `message_ids` json DEFAULT NULL,
  `rag_hits` int NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `idx_agent_run_user` (`user_id`),
  KEY `idx_agent_run_classroom` (`classroom_id`,`id`),
  CONSTRAINT `agent_runs_ibfk_1` FOREIGN KEY (`classroom_id`) REFERENCES `classrooms` (`id`) ON DELETE CASCADE,
  CONSTRAINT `agent_runs_ibfk_2` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB AUTO_INCREMENT=11 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `agent_runs`
--

LOCK TABLES `agent_runs` WRITE;
/*!40000 ALTER TABLE `agent_runs` DISABLE KEYS */;
INSERT INTO `agent_runs` VALUES (1,NULL,NULL,'v1','二分查找的时间复杂度','[\"tutor\"]',0,'',1009,'[]',0,'2026-10-31 14:07:54'),(2,NULL,NULL,'v1','tcp 三次握手','[\"tutor\"]',0,'',9844,'[]',4,'2026-10-31 14:14:33'),(3,2,2,'group','tcp与udp的区别','[\"tutor\", \"peer\"]',0,'',27379,'[2, 3]',7,'2026-10-31 14:27:26'),(4,2,4,'group','tcp与udp的区别','[\"tutor\", \"peer\"]',0,'',18096,'[10, 11]',7,'2026-11-01 15:17:05'),(5,2,3,'group','tcp','[\"tutor\", \"peer\"]',0,'',8306,'[13, 14]',7,'2026-11-01 15:17:49'),(6,8,5,'v1','tcp与udp的区别','[\"tutor\"]',0,'',5724,'[16]',4,'2026-11-01 15:23:59'),(7,8,3,'v1','','[\"tutor\"]',0,'',10464,'[18]',4,'2026-11-01 15:24:25'),(8,10,6,'debate','进程与线程的区别','[\"tutor\", \"peer\", \"examiner\"]',0,'',14736,'[20, 21, 22]',4,'2026-11-01 15:29:22');
/*!40000 ALTER TABLE `agent_runs` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `classroom_documents`
--

DROP TABLE IF EXISTS `classroom_documents`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `classroom_documents` (
  `id` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `classroom_id` int NOT NULL,
  `teacher_id` int DEFAULT NULL,
  `filename` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `stored_path` varchar(512) COLLATE utf8mb4_unicode_ci NOT NULL,
  `doc_hash` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `chunk_count` int NOT NULL,
  `file_bytes` int NOT NULL,
  `status` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'active',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `deleted_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `teacher_id` (`teacher_id`),
  KEY `idx_classroom_doc_classroom` (`classroom_id`,`status`),
  KEY `idx_classroom_doc_hash` (`classroom_id`,`doc_hash`),
  CONSTRAINT `classroom_documents_ibfk_1` FOREIGN KEY (`classroom_id`) REFERENCES `classrooms` (`id`) ON DELETE CASCADE,
  CONSTRAINT `classroom_documents_ibfk_2` FOREIGN KEY (`teacher_id`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `classroom_documents`
--

LOCK TABLES `classroom_documents` WRITE;
/*!40000 ALTER TABLE `classroom_documents` DISABLE KEYS */;
INSERT INTO `classroom_documents` VALUES ('7fee92cab8d0',3,3,'future-of-the-american-university-full-report(1).pdf','E:\\code\\Tutor-Chatbot-main\\backend\\classroom_docs\\3\\7fee92cab8d0_future-of-the-american-university-full-report(1).pdf','5ecd916824c1627406fb8424ef8c5be6b45701f9450f31486e04ca7ac5f15b97',1046,1788369,'active','2026-10-31 16:42:05',NULL),('9fd08479bc32',2,2,'future-of-the-american-university-full-report(1).pdf','E:\\code\\Tutor-Chatbot-main\\backend\\classroom_docs\\2\\9fd08479bc32_future-of-the-american-university-full-report(1).pdf','5ecd916824c1627406fb8424ef8c5be6b45701f9450f31486e04ca7ac5f15b97',1046,1788369,'active','2026-10-31 14:22:14',NULL),('e045f4f79473',7,3,'future-of-the-american-university-full-report(1).pdf','E:\\code\\Tutor-Chatbot-main\\backend\\classroom_docs\\7\\e045f4f79473_future-of-the-american-university-full-report(1).pdf','5ecd916824c1627406fb8424ef8c5be6b45701f9450f31486e04ca7ac5f15b97',1046,1788369,'active','2026-11-01 15:20:28',NULL);
/*!40000 ALTER TABLE `classroom_documents` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `classroom_members`
--

DROP TABLE IF EXISTS `classroom_members`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `classroom_members` (
  `id` int NOT NULL AUTO_INCREMENT,
  `classroom_id` int NOT NULL,
  `user_id` int NOT NULL,
  `role` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_classroom_user` (`classroom_id`,`user_id`),
  KEY `idx_member_user` (`user_id`),
  CONSTRAINT `classroom_members_ibfk_1` FOREIGN KEY (`classroom_id`) REFERENCES `classrooms` (`id`) ON DELETE CASCADE,
  CONSTRAINT `classroom_members_ibfk_2` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=21 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `classroom_members`
--

LOCK TABLES `classroom_members` WRITE;
/*!40000 ALTER TABLE `classroom_members` DISABLE KEYS */;
INSERT INTO `classroom_members` VALUES (1,2,2,'teacher','2026-10-31 14:18:24'),(2,2,1,'student','2026-10-31 14:37:21'),(3,2,3,'student','2026-10-31 14:53:02'),(4,3,3,'teacher','2026-10-31 16:40:23'),(5,4,3,'teacher','2026-10-31 16:42:54'),(7,6,3,'teacher','2026-10-31 17:03:59'),(8,2,4,'student','2026-11-01 15:16:04'),(9,7,3,'teacher','2026-11-01 15:18:48'),(10,8,5,'teacher','2026-11-01 15:22:29'),(11,8,3,'student','2026-11-01 15:23:44'),(12,9,5,'teacher','2026-11-01 15:25:13'),(13,8,6,'student','2026-11-01 15:25:59'),(14,7,6,'student','2026-11-01 15:26:18'),(15,10,3,'teacher','2026-11-01 15:28:42'),(16,10,6,'student','2026-11-01 15:28:55'),(17,2,6,'student','2026-11-01 15:37:56');
/*!40000 ALTER TABLE `classroom_members` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `classrooms`
--

DROP TABLE IF EXISTS `classrooms`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `classrooms` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `subject` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` text COLLATE utf8mb4_unicode_ci,
  `is_private` tinyint(1) NOT NULL,
  `scenario` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'v1',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `use_tutor` tinyint(1) NOT NULL DEFAULT '1',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=13 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `classrooms`
--

LOCK TABLES `classrooms` WRITE;
/*!40000 ALTER TABLE `classrooms` DISABLE KEYS */;
INSERT INTO `classrooms` VALUES (2,'计算机八股冲刺班','cs',NULL,0,'group','2026-10-31 14:18:24',1),(3,'计算机八股冲刺班','cs',NULL,0,'v1','2026-10-31 16:40:23',1),(4,'计算机八股冲刺班','cs',NULL,0,'v1','2026-10-31 16:42:54',1),(6,'计算机八股冲刺班','cs',NULL,0,'v1','2026-10-31 17:03:59',1),(7,'1v1','cs',NULL,0,'v1','2026-11-01 15:18:48',1),(8,'计算机八股冲刺班-3','cs',NULL,0,'v1','2026-11-01 15:22:29',1),(9,'计算机八股冲刺班','cs',NULL,0,'v1','2026-11-01 15:25:12',1),(10,'辩论对抗','cs',NULL,0,'v1','2026-11-01 15:28:42',1);
/*!40000 ALTER TABLE `classrooms` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `learning_behaviors`
--

DROP TABLE IF EXISTS `learning_behaviors`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `learning_behaviors` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `classroom_id` int NOT NULL,
  `topic` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `action` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL,
  `hint_level` int NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `idx_behavior_user_topic` (`user_id`,`topic`),
  KEY `idx_behavior_classroom` (`classroom_id`),
  CONSTRAINT `learning_behaviors_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `learning_behaviors_ibfk_2` FOREIGN KEY (`classroom_id`) REFERENCES `classrooms` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=8 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `learning_behaviors`
--

LOCK TABLES `learning_behaviors` WRITE;
/*!40000 ALTER TABLE `learning_behaviors` DISABLE KEYS */;
INSERT INTO `learning_behaviors` VALUES (1,2,2,'tcp与udp的区别','ask_question',0,'2026-10-31 14:27:26'),(2,4,2,'tcp与udp的区别','ask_question',0,'2026-11-01 15:17:05'),(3,3,2,'tcp','ask_question',0,'2026-11-01 15:17:49'),(4,5,8,'tcp与udp的区别','ask_question',0,'2026-11-01 15:23:59'),(5,6,10,'进程与线程的区别','ask_question',0,'2026-11-01 15:29:22');
/*!40000 ALTER TABLE `learning_behaviors` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `messages`
--

DROP TABLE IF EXISTS `messages`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `messages` (
  `id` int NOT NULL AUTO_INCREMENT,
  `classroom_id` int NOT NULL,
  `user_id` int DEFAULT NULL,
  `role` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `content` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `topic` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `hint_level` int NOT NULL,
  `sources` json DEFAULT NULL,
  `agent_role` varchar(16) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `user_id` (`user_id`),
  KEY `idx_message_classroom` (`classroom_id`,`id`),
  CONSTRAINT `messages_ibfk_1` FOREIGN KEY (`classroom_id`) REFERENCES `classrooms` (`id`) ON DELETE CASCADE,
  CONSTRAINT `messages_ibfk_2` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB AUTO_INCREMENT=27 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `messages`
--

LOCK TABLES `messages` WRITE;
/*!40000 ALTER TABLE `messages` DISABLE KEYS */;
INSERT INTO `messages` VALUES (1,2,2,'student','TCP 和 UDP 的区别','2026-10-31 14:26:56',NULL,0,'null',NULL),(2,2,2,'assistant','一句话结论：TCP 是面向连接、可靠、基于字节流的传输层协议；UDP 是无连接、不可靠、基于数据报的传输层协议，因此 TCP 更稳，UDP 更快。\n\n关键区别：\n\n  维度       TCP                          UDP\n  --------   ---------------------------  ---------------------------\n  连接性     面向连接，需三次握手         无连接，直接发送\n  可靠性     可靠：确认、重传、排序、去重  不可靠：尽最大努力交付\n  数据形式   字节流，无报文边界           数据报，保留报文边界\n  通信方式   仅一对一                     一对一、一对多、多对多，支持广播多播\n  头部开销   20 字节起                    8 字节\n  拥塞控制   有                           无\n  传输速度   慢                           快\n  典型用途   文件传输、Web、邮件          音视频、实时游戏、DNS 查询\n\n两点常被追问的补充：\n\n1. TCP 比 UDP 慢在哪：握手与挥手的往返、确认与重传等待、拥塞控制的保守调速、更大的头部、内核协议栈处理开销。\n\n2. 可靠性不是 TCP 的专利：可以在应用层基于 UDP 自己实现序列号、ACK、超时重传、滑动窗口和拥塞控制，QUIC（HTTP/3）就是这么做的，同时避开了 TCP 的队头阻塞和握手延迟。\n\n面试时按「连接性、可靠性、数据形式、开销与速度、适用场景」这五条答，基本就是标准答案。\n\n这一版对比是否够用，还是需要我把某一条（比如应用层实现可靠传输）展开讲？','2026-10-31 14:27:09','tcp与udp的区别',0,'[{\"topic\": \"TCP 与 UDP\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-vs-udp.md\", \"category\": \"计算机网络\"}, {\"topic\": \"SQL 查询与多表连接\", \"origin\": \"kb\", \"source\": \"cs/db/sql-query-join.md\", \"category\": \"数据库\"}, {\"topic\": \"TCP 与 UDP\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-vs-udp.md\", \"category\": \"计算机网络\"}, {\"topic\": \"SQL 查询与多表连接\", \"origin\": \"kb\", \"source\": \"cs/db/sql-query-join.md\", \"category\": \"数据库\"}, {\"page\": 103, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}, {\"page\": 186, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}, {\"page\": 176, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}]','tutor'),(3,2,2,'assistant','我补个角度，也顺带一句口诀：TCP 像「倒水」，UDP 像「寄包裹」。\n\nTCP 是字节流，你 write 两次 100 字节，对端可能一次 read 200 字节，边界要自己在应用层划；UDP 一个 sendto 对应一个 recvfrom，报文边界天然保留，代价是超过 MTU 会分片，丢了就整包丢。\n\n记个数字：以太网 MTU 1500，减 IP 头 20、TCP 头 20，MSS 就是 1460，握手时双方还会交换 MSS。这块常被追问，你手上有数吗？','2026-10-31 14:27:26','tcp与udp的区别',0,'[{\"topic\": \"TCP 与 UDP\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-vs-udp.md\", \"category\": \"计算机网络\"}, {\"topic\": \"SQL 查询与多表连接\", \"origin\": \"kb\", \"source\": \"cs/db/sql-query-join.md\", \"category\": \"数据库\"}, {\"topic\": \"TCP 与 UDP\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-vs-udp.md\", \"category\": \"计算机网络\"}, {\"topic\": \"SQL 查询与多表连接\", \"origin\": \"kb\", \"source\": \"cs/db/sql-query-join.md\", \"category\": \"数据库\"}, {\"page\": 103, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}, {\"page\": 186, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}, {\"page\": 176, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}]','peer'),(4,2,3,'student','你好','2026-10-31 14:53:19',NULL,0,'null',NULL),(5,2,3,'assistant','抱歉，这个问题超出了当前知识库的范围，我暂时无法作答。我是计算机八股（面试知识点）辅导助手，可以为你讲解操作系统、计算机网络、数据库、数据结构与算法等内容。欢迎换个相关的问题试试。','2026-10-31 14:53:22','',0,'[]','tutor'),(6,2,1,'student','那个表格里「可靠性」和「开销」能再展开说一下吗？','2026-10-31 16:48:23',NULL,0,'null',NULL),(7,2,3,'student','你好，糖果','2026-11-01 15:16:34',NULL,0,'null',NULL),(8,2,4,'student','可以具体说一下[开销]吗','2026-11-01 15:16:38',NULL,0,'null',NULL),(9,2,3,'assistant','抱歉，这个问题超出了当前知识库的范围，我暂时无法作答。我是计算机八股（面试知识点）辅导助手，可以为你讲解操作系统、计算机网络、数据库、数据结构与算法等内容。欢迎换个相关的问题试试。','2026-11-01 15:16:40','',0,'[]','tutor'),(10,2,4,'assistant','一句话结论：开销要分四层看：头部开销、建连开销、状态维护开销、机制开销；TCP 在这四层上都比 UDP 贵，但贵多少取决于流量模型。\n\n  开销类型        TCP                                    UDP\n  -------------   ------------------------------------   ----------------------------\n  头部            固定 20 字节，带选项最多 60 字节       固定 8 字节\n  建连            三次握手至少 1 个 RTT 才能发数据       0 个 RTT，直接发\n  断开            四次挥手，主动方等 2MSL                无\n  状态维护        每个连接一个 TCB、收发缓冲区、定时器    基本无状态\n  机制            确认、重传、排序、流量控制、拥塞控制    只有可选的校验和\n\n一、头部开销\nUDP 头固定 8 字节：源端口 2、目的端口 2、长度 2、校验和 2，没有选项字段，结构不会再涨。\nTCP 头固定部分 20 字节，带选项（MSS、窗口扩大、时间戳、SACK）可到 60 字节。\n关键是小报文场景：DNS 查询载荷通常只有几十字节，用 TCP 光是头部加上握手，开销就远超有效载荷，所以 DNS 默认走 UDP。反过来传大文件时，20 字节头摊到 1460 字节 MSS 上，占比不到 1.5%，头部开销就可以忽略。\n\n二、建连与断开开销\nTCP 建立连接需要三次握手，客户端最快也要 1 个 RTT 之后才能发数据；断开要四次挥手，主动关闭方还要进 TIME_WAIT 等 2MSL。\n注意 RTT 是乘性成本：跨洋链路 RTT 150ms，一个新连接光握手就 150ms 起，短连接密集的场景（比如早期 HTTP/1.0）这部分占比极高。UDP 没有这一层，一次请求一次响应总共 1 个 RTT 就完事。\n\n三、状态维护开销\nTCP 每个连接在内核里要维护 TCB，还带发送缓冲区、接收缓冲区、重传定时器、坚持定时器、TIME_WAIT 定时器。十万并发连接就是十万份状态，内存是实打实的成本。\nUDP 无连接，内核不为对端保留状态，同样的并发量内存压力小得多，这也是 QUIC 要在用户态自己管连接、而不是靠内核 TCP 的原因之一。\n\n四、机制开销\nTCP 的可靠性是靠额外动作换来的：每个字节都要被确认，丢包要重传（重传还要等 RTO 或三个重复 ACK），拥塞控制会在丢包时主动降速。这些动作既消耗带宽也消耗 CPU。\nUDP 把这些全砍掉，代价是可靠性得应用层自己补，补上之后开销会重新涨回来。关键结论：UDP 省的是传输层开销，不是总开销，QUIC 就是把这笔账挪到用户态重新算了一遍。\n\n面试话术可以收成一句：TCP 的开销买的是可靠性和有序性，UDP 的低开销买的是时延和并发，选型本质是看这笔开销值不值。\n\n这里的四层拆分清楚了吗，还是要我挑一层（比如状态维护和并发连接数）再展开？','2026-11-01 15:16:55','tcp与udp的区别',0,'[{\"topic\": \"TCP 与 UDP\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-vs-udp.md\", \"category\": \"计算机网络\"}, {\"topic\": \"TCP 三次握手与四次挥手\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-handshake.md\", \"category\": \"计算机网络\"}, {\"topic\": \"TCP/IP 分层模型与 ARP / DNS\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-ip-model.md\", \"category\": \"计算机网络\"}, {\"topic\": \"TCP 与 UDP\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-vs-udp.md\", \"category\": \"计算机网络\"}, {\"page\": 93, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}, {\"page\": 69, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}, {\"page\": 132, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}]','tutor'),(11,2,4,'assistant','你好呀。这两个点我换个切法展开，不重复助教那张四层表。\n\n一、可靠性这块，先分清两个层次\n\n1. TCP 保证的是「传输层可靠」：不丢、不重、按序。靠的是序号、确认、超时重传、重复包丢弃这一套。\n2. 但不是「业务可靠」。`write()` 返回成功，只代表数据进了本机内核发送缓冲区，对方进程有没有处理、业务有没有成功，TCP 不管。所以真正要保证业务正确，还得应用层加业务 ACK 或幂等设计，这点面试常被追问。\n3. 而且可靠是有时间代价的：丢包后要等 RTO 或者三个重复 ACK 才重传。实时场景里，这个等待累积起来比丢一帧难受得多，这就是音视频宁可走 UDP 的原因。\n\n二、开销这块，我习惯拆成三本账\n\n1. 字节账：头 20 对 8，但占比随报文变大迅速摊薄。传大文件时这部分基本可忽略，所以拿「头大」当 TCP 慢的主因其实站不住。\n2. 时间账：这块最容易被漏掉。握手 1 个 RTT、挥手等 2MSL、重传等 RTO、拥塞控制降速，全是时间。跨洋 RTT 150ms 的话，每建一次短连接就白扔 300ms 左右。所以我判断：TCP 的开销大头在时间维度，不在字节维度。\n3. 内存与 CPU 账：TCB、收发缓冲区、好几个定时器，十万并发就是十万份；再加上校验和计算和数据拷贝。\n\n补一句：UDP 省下来的只是传输层这部分开销。真要可靠，QUIC 会自己把序号、ACK、重传再补一遍，开销回来一部分，但它能只对需要的流做、还能挪到用户态少一次跨界，所以整体仍然划算。\n\n你更想深挖哪本账？时间账我可以拿一个短连接密集的场景陪你算一遍。','2026-11-01 15:17:05','tcp与udp的区别',0,'[{\"topic\": \"TCP 与 UDP\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-vs-udp.md\", \"category\": \"计算机网络\"}, {\"topic\": \"TCP 三次握手与四次挥手\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-handshake.md\", \"category\": \"计算机网络\"}, {\"topic\": \"TCP/IP 分层模型与 ARP / DNS\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-ip-model.md\", \"category\": \"计算机网络\"}, {\"topic\": \"TCP 与 UDP\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-vs-udp.md\", \"category\": \"计算机网络\"}, {\"page\": 93, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}, {\"page\": 69, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}, {\"page\": 132, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}]','peer'),(12,2,3,'student','tcp是什么','2026-11-01 15:17:24',NULL,0,'null',NULL),(13,2,3,'assistant','一句话结论：TCP（Transmission Control Protocol，传输控制协议）是传输层的一种面向连接的、可靠的、基于字节流的传输协议。\n\n一、所处层次\n位于 IP 层之上、应用层之下，为 HTTP、FTP、SMTP 等应用层协议提供端到端的传输服务。\n\n二、四个核心特征\n1. 面向连接：通信前必须三次握手建立连接，结束后四次挥手释放。\n2. 可靠传输：通过序号、确认、超时重传、去重排序，保证数据不丢、不重、按序到达。\n3. 基于字节流：没有报文边界，发送方 write 的次数与接收方 read 的次数不对应。\n4. 一对一通信：一条连接只对应两个端点，不支持广播和多播。\n\n三、它解决的问题\nIP 层只负责尽力而为地把包送到主机，不保证顺序、不保证到达、也不区分进程。TCP 在其上补齐三件事：可靠性、有序性，以及通过端口号实现的进程到进程的区分（复用与分用）。\n\n四、连接的唯一标识\n四元组：源 IP、源端口、目的 IP、目的端口。\n\n这块是后面握手挥手、可靠传输、流量拥塞控制所有内容的地基，定义先记牢。\n\n要不要接着往下讲三次握手，还是先把「字节流」这个特征讲透？','2026-11-01 15:17:44','tcp',0,'[{\"topic\": \"TCP/IP 分层模型与 ARP / DNS\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-ip-model.md\", \"category\": \"计算机网络\"}, {\"topic\": \"TCP 与 UDP\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-vs-udp.md\", \"category\": \"计算机网络\"}, {\"topic\": \"TCP/IP 分层模型与 ARP / DNS\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-ip-model.md\", \"category\": \"计算机网络\"}, {\"topic\": \"TCP 三次握手与四次挥手\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-handshake.md\", \"category\": \"计算机网络\"}, {\"page\": 34, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}, {\"page\": 67, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}, {\"page\": 15, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}]','tutor'),(14,2,3,'assistant','换个说法：TCP 更像两个人在打电话前先对暗号，之后各自拿小本本记进度：记下自己发到第几号、收到第几号、对方还能接多少。连接只存在于两端内核里，中间的路由器完全不知道有这么一条「连接」，它们看到的还是一个个独立的 IP 包。\n\n所以严格说，TCP 是两端各维护一份状态机，合起来给上层造出「一条可靠管道」的错觉。\n\n好记的口诀：握手对齐、序号有序、窗口限流、拥塞降速、挥手等 2MSL。\n\n这个「连接是两端的状态约定」的角度，你觉得比「面向连接」四个字更好抓吗？','2026-11-01 15:17:49','tcp',0,'[{\"topic\": \"TCP/IP 分层模型与 ARP / DNS\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-ip-model.md\", \"category\": \"计算机网络\"}, {\"topic\": \"TCP 与 UDP\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-vs-udp.md\", \"category\": \"计算机网络\"}, {\"topic\": \"TCP/IP 分层模型与 ARP / DNS\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-ip-model.md\", \"category\": \"计算机网络\"}, {\"topic\": \"TCP 三次握手与四次挥手\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-handshake.md\", \"category\": \"计算机网络\"}, {\"page\": 34, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}, {\"page\": 67, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}, {\"page\": 15, \"doc_id\": \"9fd08479bc32\", \"origin\": \"classroom\", \"source\": \"classroom:2/9fd08479bc32_future-of-the-american-university-full-report(1).pdf\", \"section\": \"body\", \"filename\": \"future-of-the-american-university-full-report(1).pdf\"}]','peer'),(15,8,5,'student','TCP 和 UDP 的区别','2026-11-01 15:23:51',NULL,0,'null',NULL),(16,8,5,'assistant','一句话结论：TCP 是面向连接、可靠、基于字节流的传输层协议，UDP 是无连接、不可靠（尽力而为）、保留报文边界的数据报协议；二者的本质区别在于，可靠性、连接管理、拥塞控制这些机制都被放在TCP 协议栈里，而 UDP 把这些机制交给应用层自己决定。\n\n一、核心区别对照\n\n维度          TCP                        UDP\n------------  -------------------------  ----------------------------\n连接          面向连接，三次握手建连      无连接，直接发\n可靠性        可靠：确认、重传、排序      不可靠：不保证送达和顺序\n传输方式      字节流，无报文边界          数据报，保留报文边界\n流量/拥塞控制  有：滑动窗口、拥塞控制      无\n头部开销      20 字节起                   8 字节\n通信模式      一对一                      一对一、一对多、多播、广播\n速度          慢                          快\n\n二、TCP 比 UDP 慢在哪\n\n1. 握手和挥手的往返开销。\n2. 确认与重传的等待。\n3. 拥塞控制的保守调速。\n4. 更大的头部。\n5. 内核协议栈的处理开销更大。\n\n三、关于「UDP 不可靠」的正确理解\n\n可靠性不是 TCP 独有的特性；可以在应用层用 UDP 自己实现：序列号、ACK 确认、超时重传、滑动窗口、拥塞控制。QUIC（HTTP/3）就运行在 UDP 上，自行实现了可靠传输与拥塞控制，同时避开了 TCP 的队头阻塞和握手延迟。参考实现还有 RTP/RTCP。\n\n四、典型选型\n\n1. 用 TCP：文件传输、网页、邮件等要求数据完整有序的场景。\n2. 用 UDP：DNS 查询（报文通常小于 512 字节，一次往返完成；响应超过 512 字节或做区域传送时才用 TCP）、音视频、直播、游戏等要求低时延、能容忍少量丢包的场景。\n\n补充一点常见追问：TCP 的 MSS = MTU（以太网 1500）− IP 头 20 − TCP 头 20 = 1460 字节，握手时双方交换 MSS。\n\n以上这些区别，你能对上号吗？','2026-11-01 15:23:59','tcp与udp的区别',0,'[{\"topic\": \"TCP 与 UDP\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-vs-udp.md\", \"category\": \"计算机网络\"}, {\"topic\": \"SQL 查询与多表连接\", \"origin\": \"kb\", \"source\": \"cs/db/sql-query-join.md\", \"category\": \"数据库\"}, {\"topic\": \"TCP 与 UDP\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-vs-udp.md\", \"category\": \"计算机网络\"}, {\"topic\": \"SQL 查询与多表连接\", \"origin\": \"kb\", \"source\": \"cs/db/sql-query-join.md\", \"category\": \"数据库\"}]','tutor'),(17,8,3,'student','不能','2026-11-01 15:24:04',NULL,0,'null',NULL),(18,8,3,'assistant','关键是要抓住一个根本原因：TCP 在两端内核里维护连接状态，UDP 不维护。所有区别都是由这个原因派生出来的，不是并列的八个知识点。\n\n一、根本原因：有没有「连接状态」\n\nTCP 建立连接时，两端内核各自记下一份状态：四元组（源 IP、源端口、目的 IP、目的端口）、序列号、窗口大小、拥塞窗口、重传计时器。这条连接在关闭前一直存在。\n\nUDP 发一个报文，填上目标地址就交给 IP 层，内核不记任何东西，发完即忘。\n\n二、由根本原因派生的各维度\n\n下面的每一行，都请你这样读：因为 TCP 维护了状态，所以能实现这个机制；UDP 不维护状态，所以做不到，或者只能由应用层自己做。\n\n1. 可靠性。TCP 内核记录了自己发出去的序列号，所以能等 ACK、超时重传、按序号重排、丢弃重复包。UDP 没有记录，发出去就不管了，丢了就是丢了。\n\n2. 传输方式。TCP 把数据当字节流写进发送缓冲区，内核按自己的节奏切包、合并包，所以没有报文边界。UDP 每次 sendto 就是一个独立数据报，内核原样封装，收端一次 recvfrom 就拿到一个完整报文，边界保留。\n\n3. 流量控制和拥塞控制。TCP 记录了接收方窗口和拥塞窗口，所以知道对方吃得下多少、网络能承受多少，发慢了可以加速，丢包了就减速。UDP 没有这些记录，只能全速发。\n\n4. 连接管理。TCP 要有状态，就得先把状态建起来（三次握手同步初始序列号、确认双方收发能力），不用了要拆掉（四次挥手）。UDP 不用建也不用拆。\n\n5. 通信模式。TCP 的一条连接由四元组唯一确定，只能一对一：多个客户端就是多条连接。UDP 每个报文自带目标地址，无状态，所以天然能做一对多、多播、广播。\n\n6. 头部与开销。TCP 要用头部字段承载序列号、ACK 号、窗口、标志位，20 字节起。UDP 只需要端口和长度校验，8 字节。\n\n三、用一个场景把对应关系串起来\n\n假设应用层要发两条消息：「AB」和「CD」。\n\nTCP 这边：你调两次 send，数据进缓冲区，内核可能合成一个段「ABCD」发出去。接收端一次 recv 可能读到「ABCD」，也可能读到「A」「BCD」。所以 TCP 必须由应用层自己做分帧（长度前缀或分隔符），否则无法区分消息边界。这就是面试常说的「TCP 粘包」；注意它不是 TCP 的 bug，而是字节流的固有性质。\n\nUDP 这边：你调两次 sendto，接收端就调两次 recvfrom，第一次拿到「AB」，第二次拿到「CD」，边界天然保留。代价是报文如果超过 MTU（以太网 1500 字节）会被 IP 分片，任一分片丢失，整个报文在接收端被丢弃。\n\n反过来，可靠性上：TCP 这段「ABCD」丢了会重传，你不需要管。UDP 的「AB」丢了，应用层完全不知道，要么不管（语音通话，丢一小段听不出来），要么自己在应用层加序号和重传（QUIC 就是这么干的）。\n\n四、自查追问\n\nUDP 会不会出现「粘包」？如果不会，为什么有些基于 UDP 的协议（比如 QUIC）还要在头部里放长度字段和包序号？','2026-11-01 15:24:25','',3,'[{\"topic\": \"SQL 查询与多表连接\", \"origin\": \"kb\", \"source\": \"cs/db/sql-query-join.md\", \"category\": \"数据库\"}, {\"topic\": \"TCP 三次握手与四次挥手\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-handshake.md\", \"category\": \"计算机网络\"}, {\"topic\": \"文件系统：inode、目录与软硬链接\", \"origin\": \"kb\", \"source\": \"cs/os/filesystem.md\", \"category\": \"操作系统\"}, {\"topic\": \"TCP 三次握手与四次挥手\", \"origin\": \"kb\", \"source\": \"cs/network/tcp-handshake.md\", \"category\": \"计算机网络\"}]','tutor'),(19,10,6,'student','进程和线程有什么区别？','2026-11-01 15:29:04',NULL,0,'null',NULL),(20,10,6,'assistant','一句话结论：进程是资源分配的基本单位，线程是 CPU 调度的基本单位；同一进程内的线程共享地址空间，各自拥有独立的栈和寄存器上下文。\n\n一、核心区别\n\n  维度      进程                                线程\n  --------  ----------------------------------  ----------------------------------\n  定义      程序的一次执行过程                  进程内的执行流\n  地址空间  独立的虚拟地址空间                  共享所属进程的地址空间\n  切换开销  大，要切换页表、刷新 TLB、换内核栈  小，只需保存寄存器和栈指针\n  通信方式  需要 IPC：管道、消息队列、共享内存、信号量、Socket  直接读写共享变量，但要配互斥锁\n  健壮性    一个进程崩溃不影响其他进程          一个线程崩溃会导致整个进程退出\n  创建开销  大，需要分配独立内存空间            小\n\n二、引入线程的原因\n\n关键是进程切换的代价太高：每次切换都要换页表基址寄存器 CR3，导致 TLB 失效、Cache 命中率骤降；线程共享地址空间，切换时这些都不用动。另外，程序内部常有多个并发任务（一边下载一边渲染），用多进程做通信成本高，用线程共享内存天然方便。\n\n三、多进程与多线程的选择\n\n1. 需要强隔离、稳定性优先（如浏览器多标签页）：选多进程。\n2. 需要频繁共享数据、性能优先：选多线程。\n3. 实际工程常见「多进程 + 多线程」混合。\n\n补充一点资料外常考的：进程状态为创建、就绪、运行、阻塞、终止；「就绪」是缺 CPU，「阻塞」是缺事件（如等 IO）。\n\n你对线程共享什么、独占什么这块清楚吗？','2026-11-01 15:29:10','进程与线程的区别',0,'[{\"topic\": \"进程与线程\", \"origin\": \"kb\", \"source\": \"cs/os/process-thread.md\", \"category\": \"操作系统\"}, {\"topic\": \"进程与线程\", \"origin\": \"kb\", \"source\": \"cs/os/process-thread.md\", \"category\": \"操作系统\"}, {\"topic\": \"进程与线程\", \"origin\": \"kb\", \"source\": \"cs/os/process-thread.md\", \"category\": \"操作系统\"}, {\"topic\": \"进程与线程\", \"origin\": \"kb\", \"source\": \"cs/os/process-thread.md\", \"category\": \"操作系统\"}]','tutor'),(21,10,6,'assistant','我补三个边界，它们的共同点是：都只在特定前提下成立。\n\n一、「线程切换便宜」只在同一个进程内成立；跨进程的两个线程切换时，照样要换 CR3、刷 TLB。\n\n二、「线程是 CPU 调度基本单位」在用户级线程实现下不成立：内核看不见线程，调度单位仍是进程。\n\n三、「进程隔离、崩溃不影响别人」遇到共享内存、文件锁就会漏，邻居写坏共享段照样崩。\n\n所以这三条都是「通常成立」，不能当定义背。','2026-11-01 15:29:15','进程与线程的区别',0,'[{\"topic\": \"进程与线程\", \"origin\": \"kb\", \"source\": \"cs/os/process-thread.md\", \"category\": \"操作系统\"}, {\"topic\": \"进程与线程\", \"origin\": \"kb\", \"source\": \"cs/os/process-thread.md\", \"category\": \"操作系统\"}, {\"topic\": \"进程与线程\", \"origin\": \"kb\", \"source\": \"cs/os/process-thread.md\", \"category\": \"操作系统\"}, {\"topic\": \"进程与线程\", \"origin\": \"kb\", \"source\": \"cs/os/process-thread.md\", \"category\": \"操作系统\"}]','peer'),(22,10,6,'assistant','你只是把题目复述了一遍，没有实际作答，等于没答。\n\n追问一道：同一进程的两个线程分别跑在两个核上，其中一个被调度下去时，内核到底「省」了哪一步？请指到具体的寄存器或结构。\n\n判断标准：只答「不用换页表」的是背的；能说清省掉的是 CR3 重载与 TLB 失效、并讲出共享页表要付的代价，才算真懂。','2026-11-01 15:29:22','进程与线程的区别',0,'[{\"topic\": \"进程与线程\", \"origin\": \"kb\", \"source\": \"cs/os/process-thread.md\", \"category\": \"操作系统\"}, {\"topic\": \"进程与线程\", \"origin\": \"kb\", \"source\": \"cs/os/process-thread.md\", \"category\": \"操作系统\"}, {\"topic\": \"进程与线程\", \"origin\": \"kb\", \"source\": \"cs/os/process-thread.md\", \"category\": \"操作系统\"}, {\"topic\": \"进程与线程\", \"origin\": \"kb\", \"source\": \"cs/os/process-thread.md\", \"category\": \"操作系统\"}]','examiner');
/*!40000 ALTER TABLE `messages` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `student_profiles`
--

DROP TABLE IF EXISTS `student_profiles`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `student_profiles` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `classroom_id` int NOT NULL,
  `overall_level` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `learning_preference` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `knowledge_mastery` json NOT NULL,
  `weak_points` json NOT NULL,
  `ability_vector` json DEFAULT NULL,
  `cognitive_style` json DEFAULT NULL,
  `pretested_at` datetime DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_profile_user_classroom` (`user_id`,`classroom_id`),
  KEY `idx_profile_classroom` (`classroom_id`),
  CONSTRAINT `student_profiles_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `student_profiles_ibfk_2` FOREIGN KEY (`classroom_id`) REFERENCES `classrooms` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=7 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `student_profiles`
--

LOCK TABLES `student_profiles` WRITE;
/*!40000 ALTER TABLE `student_profiles` DISABLE KEYS */;
INSERT INTO `student_profiles` VALUES (1,2,2,'intermediate','暂无明显偏好，按标准讲法即可','{\"tcp与udp的区别\": 0.5}','[]','{}',NULL,NULL,'2026-10-31 14:27:26'),(2,4,2,'intermediate','暂无明显偏好，按标准讲法即可','{\"tcp与udp的区别\": 0.5}','[]','{}',NULL,NULL,'2026-11-01 15:17:05'),(3,3,2,'intermediate','暂无明显偏好，按标准讲法即可','{\"tcp\": 0.5}','[]','{}',NULL,NULL,'2026-11-01 15:17:49'),(4,5,8,'intermediate','暂无明显偏好，按标准讲法即可','{\"tcp与udp的区别\": 0.5}','[]','{}',NULL,NULL,'2026-11-01 15:23:59'),(5,6,10,'intermediate','暂无明显偏好，按标准讲法即可','{\"进程与线程的区别\": 0.5}','[]','{}',NULL,NULL,'2026-11-01 15:29:22');
/*!40000 ALTER TABLE `student_profiles` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `users`
--

DROP TABLE IF EXISTS `users`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `users` (
  `id` int NOT NULL AUTO_INCREMENT,
  `username` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `role` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `username` (`username`)
) ENGINE=InnoDB AUTO_INCREMENT=10 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `users`
--

LOCK TABLES `users` WRITE;
/*!40000 ALTER TABLE `users` DISABLE KEYS */;
INSERT INTO `users` VALUES (1,'张旭','student','2026-10-31 14:17:45'),(2,'水水','teacher','2026-10-31 14:18:07'),(3,'皇后','student','2026-10-31 14:53:02'),(4,'糖果','student','2026-11-01 15:16:04'),(5,'老师','teacher','2026-11-01 15:22:03'),(6,'小明','student','2026-11-01 15:25:59');
/*!40000 ALTER TABLE `users` ENABLE KEYS */;
UNLOCK TABLES;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-11-01 17:14:36
