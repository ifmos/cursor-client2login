#!/usr/bin/env python3
import json
import sys
import struct
import sqlite3
import os
import platform
import stat
import time
import uuid
import secrets
import hashlib
import base64
import requests
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable, Tuple
from abc import ABC, abstractmethod

try:
    import nativemessaging
    NATIVEMESSAGING_AVAILABLE = True
except ImportError:
    NATIVEMESSAGING_AVAILABLE = False


class BaseActionHandler(ABC):
    """Action处理器基类"""
    
    @abstractmethod
    def handle(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """处理请求并返回响应"""
        pass


class CursorDataManager:
    """Cursor数据管理器"""

    @staticmethod
    def get_cursor_db_path() -> str:
        """根据操作系统获取Cursor数据库路径"""
        system = platform.system()

        if system == "Windows":
            appdata = os.getenv("APPDATA")
            if appdata is None:
                raise EnvironmentError("APPDATA 环境变量未设置")
            return os.path.join(appdata, "Cursor", "User", "globalStorage", "state.vscdb")
        elif system == "Darwin":  # macOS
            return os.path.expanduser("~/Library/Application Support/Cursor/User/globalStorage/state.vscdb")
        elif system == "Linux":
            return os.path.expanduser("~/.config/Cursor/User/globalStorage/state.vscdb")
        else:
            raise NotImplementedError(f"不支持的操作系统: {system}")

    @staticmethod
    def check_file_permissions(file_path: str) -> Dict[str, Any]:
        """检查文件权限和可访问性"""
        try:
            if not os.path.exists(file_path):
                return {
                    "accessible": False,
                    "error": f"文件不存在: {file_path}",
                    "suggestions": [
                        "确保Cursor已安装并至少运行过一次",
                        "检查Cursor是否已登录过账户",
                        "验证文件路径是否正确"
                    ]
                }

            # 检查文件权限
            file_stat = os.stat(file_path)
            file_mode = file_stat.st_mode

            # 检查读权限
            if not os.access(file_path, os.R_OK):
                return {
                    "accessible": False,
                    "error": f"文件无读取权限: {file_path}",
                    "file_mode": oct(file_mode),
                    "suggestions": [
                        f"尝试修改文件权限: chmod 644 '{file_path}'",
                        "检查文件是否被其他程序占用",
                        "以管理员权限运行程序"
                    ]
                }

            # 检查文件大小
            file_size = file_stat.st_size
            if file_size == 0:
                return {
                    "accessible": False,
                    "error": f"文件为空: {file_path}",
                    "suggestions": [
                        "重新启动Cursor应用程序",
                        "重新登录Cursor账户",
                        "检查Cursor是否正常工作"
                    ]
                }

            return {
                "accessible": True,
                "file_size": file_size,
                "file_mode": oct(file_mode),
                "last_modified": file_stat.st_mtime
            }

        except PermissionError as e:
            return {
                "accessible": False,
                "error": f"权限错误: {str(e)}",
                "suggestions": [
                    "以管理员权限运行程序",
                    "检查文件权限设置",
                    "确保当前用户有访问权限"
                ]
            }
        except Exception as e:
            return {
                "accessible": False,
                "error": f"检查文件权限时发生错误: {str(e)}",
                "suggestions": [
                    "检查文件路径是否正确",
                    "确保文件系统正常",
                    "重试操作"
                ]
            }

    @staticmethod
    def get_scope_json_path() -> str:
        """根据操作系统获取scope_v3.json路径"""
        system = platform.system()
        
        if system == "Windows":
            appdata = os.getenv("APPDATA")
            if appdata is None:
                raise EnvironmentError("APPDATA 环境变量未设置")
            return os.path.join(appdata, "Cursor", "sentry", "scope_v3.json")
        elif system == "Darwin":  # macOS
            return os.path.expanduser("~/Library/Application Support/Cursor/sentry/scope_v3.json")
        elif system == "Linux":
            return os.path.expanduser("~/.config/Cursor/sentry/scope_v3.json")
        else:
            raise NotImplementedError(f"不支持的操作系统: {system}")

    @classmethod
    def read_access_token(cls) -> Dict[str, Any]:
        """从Cursor数据库读取accessToken"""
        try:
            db_path = cls.get_cursor_db_path()

            # 检查文件权限和可访问性
            permission_check = cls.check_file_permissions(db_path)
            if not permission_check["accessible"]:
                return {
                    "error": permission_check["error"],
                    "suggestions": permission_check.get("suggestions", []),
                    "file_path": db_path
                }

            # 尝试连接数据库（只读模式，避免影响 Cursor 自身的写入）
            conn = None
            try:
                db_uri = Path(db_path).as_uri() + "?mode=ro"
                conn = sqlite3.connect(db_uri, uri=True, timeout=5.0)
                cursor = conn.cursor()

                # 检查表是否存在 (注意：表名是ItemTable，首字母大写)
                cursor.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='ItemTable'
                """)
                if not cursor.fetchone():
                    return {
                        "error": "数据库中未找到ItemTable表",
                        "suggestions": [
                            "确保Cursor已正确安装并运行过",
                            "检查数据库文件是否完整",
                            "尝试重新启动Cursor应用"
                        ],
                        "file_path": db_path
                    }

                # 查询accessToken
                cursor.execute("SELECT value FROM ItemTable WHERE key = ?", ("cursorAuth/accessToken",))
                result = cursor.fetchone()

                if result and result[0]:
                    return {"accessToken": result[0]}
                else:
                    return {
                        "error": "未找到accessToken或token为空",
                        "suggestions": [
                            "确保已在Cursor中登录账户",
                            "尝试重新登录Cursor",
                            "检查网络连接是否正常"
                        ],
                        "file_path": db_path
                    }

            except sqlite3.OperationalError as e:
                error_msg = str(e).lower()
                if "database is locked" in error_msg:
                    return {
                        "error": "数据库被锁定，可能Cursor正在运行",
                        "suggestions": [
                            "关闭Cursor应用程序后重试",
                            "等待几秒钟后重试",
                            "检查是否有其他程序在访问数据库"
                        ],
                        "file_path": db_path,
                        "technical_error": str(e)
                    }
                elif "no such table" in error_msg:
                    return {
                        "error": "数据库表结构异常",
                        "suggestions": [
                            "数据库可能已损坏，尝试重新安装Cursor",
                            "检查Cursor版本是否兼容",
                            "备份数据后重置Cursor配置"
                        ],
                        "file_path": db_path,
                        "technical_error": str(e)
                    }
                else:
                    return {
                        "error": f"数据库操作错误: {str(e)}",
                        "suggestions": [
                            "检查数据库文件是否损坏",
                            "尝试重新启动Cursor",
                            "检查磁盘空间是否充足"
                        ],
                        "file_path": db_path,
                        "technical_error": str(e)
                    }

            except sqlite3.DatabaseError as e:
                return {
                    "error": f"数据库错误: {str(e)}",
                    "suggestions": [
                        "数据库文件可能已损坏",
                        "尝试重新安装Cursor",
                        "检查文件系统是否正常"
                    ],
                    "file_path": db_path,
                    "technical_error": str(e)
                }

            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass  # 忽略关闭连接时的错误

        except Exception as e:
            return {
                "error": f"读取accessToken时发生未预期错误: {str(e)}",
                "suggestions": [
                    "检查系统权限设置",
                    "确保Python有足够权限访问文件",
                    "重启系统后重试"
                ],
                "technical_error": str(e)
            }

    @classmethod
    def read_scope_json(cls) -> Dict[str, Any]:
        """读取scope_v3.json文件"""
        try:
            json_path = cls.get_scope_json_path()

            # 检查文件权限和可访问性
            permission_check = cls.check_file_permissions(json_path)
            if not permission_check["accessible"]:
                return {
                    "error": permission_check["error"],
                    "suggestions": permission_check.get("suggestions", []),
                    "file_path": json_path
                }

            # 尝试读取文件
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # 检查文件内容是否为空
                if not content.strip():
                    return {
                        "error": "JSON文件内容为空",
                        "suggestions": [
                            "重新启动Cursor应用程序",
                            "重新登录Cursor账户",
                            "检查Cursor是否正常运行"
                        ],
                        "file_path": json_path
                    }

                # 移除末尾的%符号（如果存在）
                content = content.rstrip('%').strip()

                # 尝试解析JSON
                try:
                    data = json.loads(content)
                except json.JSONDecodeError as e:
                    return {
                        "error": f"JSON格式错误: {str(e)}",
                        "suggestions": [
                            "文件可能已损坏，尝试重新登录Cursor",
                            "检查文件是否被意外修改",
                            "重新启动Cursor应用程序"
                        ],
                        "file_path": json_path,
                        "technical_error": str(e)
                    }

                # 验证JSON结构
                if not isinstance(data, dict):
                    return {
                        "error": "JSON文件格式不正确，根元素应为对象",
                        "suggestions": [
                            "文件结构异常，尝试重新登录Cursor",
                            "检查Cursor版本是否兼容"
                        ],
                        "file_path": json_path
                    }

                # 提取email和userid
                scope_data = data.get("scope")
                if not scope_data or not isinstance(scope_data, dict):
                    return {
                        "error": "JSON文件中缺少scope字段或格式错误",
                        "suggestions": [
                            "确保已在Cursor中完成登录",
                            "尝试重新登录Cursor账户",
                            "检查账户状态是否正常"
                        ],
                        "file_path": json_path
                    }

                user_info = scope_data.get("user")
                if not user_info or not isinstance(user_info, dict):
                    return {
                        "error": "JSON文件中缺少用户信息或格式错误",
                        "suggestions": [
                            "确保已在Cursor中完成登录",
                            "检查账户信息是否完整",
                            "尝试重新登录Cursor账户"
                        ],
                        "file_path": json_path
                    }

                email = user_info.get("email")
                user_id_full = user_info.get("id")

                if not email:
                    return {
                        "error": "未找到邮箱信息",
                        "suggestions": [
                            "确保使用邮箱登录Cursor",
                            "检查账户信息是否完整",
                            "尝试重新登录"
                        ],
                        "file_path": json_path
                    }

                if not user_id_full or not isinstance(user_id_full, str) or "|" not in user_id_full:
                    return {
                        "error": "用户ID格式不正确或缺失",
                        "suggestions": [
                            "用户ID应包含'|'分隔符",
                            "尝试重新登录Cursor账户",
                            "检查账户状态是否正常"
                        ],
                        "file_path": json_path,
                        "found_id": user_id_full
                    }

                userid = user_id_full.split("|")[1]
                if not userid:
                    return {
                        "error": "无法从用户ID中提取有效的userid",
                        "suggestions": [
                            "用户ID格式异常",
                            "尝试重新登录Cursor账户"
                        ],
                        "file_path": json_path,
                        "found_id": user_id_full
                    }

                return {
                    "email": email,
                    "userid": userid
                }

            except PermissionError as e:
                return {
                    "error": f"文件权限错误: {str(e)}",
                    "suggestions": [
                        "以管理员权限运行程序",
                        "检查文件权限设置",
                        "确保当前用户有读取权限"
                    ],
                    "file_path": json_path,
                    "technical_error": str(e)
                }

            except IOError as e:
                return {
                    "error": f"文件读取错误: {str(e)}",
                    "suggestions": [
                        "检查磁盘空间是否充足",
                        "确保文件未被其他程序占用",
                        "检查文件系统是否正常"
                    ],
                    "file_path": json_path,
                    "technical_error": str(e)
                }

        except Exception as e:
            return {
                "error": f"读取scope_v3.json时发生未预期错误: {str(e)}",
                "suggestions": [
                    "检查系统权限设置",
                    "确保Python有足够权限访问文件",
                    "重启系统后重试"
                ],
                "technical_error": str(e)
            }


class DeepTokenManager:
    """深度Token管理器"""
    
    @staticmethod
    def _generate_pkce_pair() -> Tuple[str, str]:
        """生成PKCE验证对"""
        code_verifier = secrets.token_urlsafe(43)
        code_challenge_digest = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        code_challenge = base64.urlsafe_b64encode(code_challenge_digest).decode('utf-8').rstrip('=')    
        return code_verifier, code_challenge
    
    @classmethod
    def get_deep_token_headless(cls, access_token: str, userid: str, max_attempts: int = 5) -> Dict[str, Any]:
        """
        无头模式获取深度token

        ========================================
        此方法暂时被禁用
        ========================================
        原因：无头模式实现存在问题，需要完善后再启用
        恢复方法：修复下面的实现逻辑，并取消相关调用处的注释
        相关文件：
        - popup.html 中的无头模式选项
        - popup.js 中的 handleAutoRead 方法
        - background.js 中的 getDeepToken 方法
        ========================================

        Args:
            access_token: 客户端访问token
            userid: 用户ID
            max_attempts: 最大尝试次数

        Returns:
            Dict[str, Any]: 包含深度token信息或错误信息的字典
        """
        try:
            session_cookie = f"{userid}%3A%3A{access_token}"
            
            for attempt in range(max_attempts):
                try:
                    verifier, challenge = cls._generate_pkce_pair()
                    uuid_str = str(uuid.uuid4())
                    
                    # 构造深度登录URL
                    auth_url = f"https://www.cursor.com/cn/loginDeepControl?challenge={challenge}&uuid={uuid_str}&mode=login"
                    
                    # 设置请求头，模拟浏览器
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Cursor/0.48.6 Chrome/132.0.6834.210 Electron/34.3.4 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.5",
                        "Accept-Encoding": "gzip, deflate, br",
                        "Connection": "keep-alive",
                        "Upgrade-Insecure-Requests": "1",
                        "Cookie": f"WorkosCursorSessionToken={session_cookie}"
                    }
                    
                    # 访问深度登录页面，模拟自动确认登录
                    response = requests.get(auth_url, headers=headers, timeout=10, allow_redirects=True)
                    
                    if response.status_code == 200:
                        # 短暂等待，然后轮询认证状态
                        time.sleep(2)
                        
                        # 轮询认证结果
                        poll_url = f"https://api2.cursor.sh/auth/poll?uuid={uuid_str}&verifier={verifier}"
                        poll_headers = {
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Cursor/0.48.6 Chrome/132.0.6834.210 Electron/34.3.4 Safari/537.36",
                            "Accept": "*/*",
                            "Referer": "https://www.cursor.com/"
                        }
                        
                        poll_response = requests.get(poll_url, headers=poll_headers, timeout=30)
                        
                        if poll_response.status_code == 200:
                            data = poll_response.json()
                            deep_access_token = data.get("accessToken")
                            auth_id = data.get("authId", "")
                            
                            if deep_access_token:
                                # 提取用户ID
                                deep_userid = ""
                                if len(auth_id.split("|")) > 1:
                                    deep_userid = auth_id.split("|")[1]
                                
                                # 计算过期时间（60天）
                                created_time = datetime.now()
                                expires_time = created_time + timedelta(days=60)
                                
                                return {
                                    "success": True,
                                    "accessToken": deep_access_token,
                                    "userid": deep_userid or userid,  # 如果无法提取，使用原始userid
                                    "WorkosCursorSessionToken": f"{deep_userid or userid}%3A%3A{deep_access_token}",
                                    "createdTime": created_time.isoformat(),
                                    "expiresTime": expires_time.isoformat(),
                                    "tokenType": "deep",
                                    "validDays": 60
                                }
                        else:
                            # 轮询请求失败，静默重试
                            pass
                    else:
                        # 深度登录页面访问失败，静默重试
                        pass
                    
                except requests.RequestException as e:
                    # 请求失败，静默重试
                    if attempt < max_attempts - 1:
                        time.sleep(2)  # 重试前等待
            
            return {
                "success": False,
                "error": f"无头模式获取深度token失败，已尝试 {max_attempts} 次",
                "suggestions": [
                    "检查网络连接是否正常",
                    "确认客户端token是否有效",
                    "尝试使用非无头模式"
                ]
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"无头模式获取深度token时发生错误: {str(e)}",
                "suggestions": [
                    "检查输入参数是否正确",
                    "确认网络连接状态",
                    "尝试重新获取客户端token"
                ]
            }


class GetAccessTokenHandler(BaseActionHandler):
    """获取AccessToken处理器"""

    def handle(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # params参数保留用于未来扩展，当前不使用
        _ = params  # 显式标记参数已知但未使用
        return CursorDataManager.read_access_token()


class GetScopeDataHandler(BaseActionHandler):
    """获取Scope数据处理器"""

    def handle(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # params参数保留用于未来扩展，当前不使用
        _ = params  # 显式标记参数已知但未使用
        return CursorDataManager.read_scope_json()


class GetDeepTokenHandler(BaseActionHandler):
    """获取深度Token处理器"""

    def handle(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        获取深度token

        params应包含:
        - access_token: str, 客户端访问token (可选，如果不提供则自动获取)
        - userid: str, 用户ID (可选，如果不提供则自动获取)

        注意：headless参数暂时禁用，强制使用浏览器模式
        """
        # headless = params.get("headless", True)  # 暂时注释掉，强制使用浏览器模式
        access_token = params.get("access_token")
        userid = params.get("userid")
        
        # 如果没有提供token或userid，先获取客户端数据
        if not access_token or not userid:
            client_data_handler = GetClientCurrentDataHandler()
            client_result = client_data_handler.handle({})
            
            if "error" in client_result:
                return {
                    "error": f"获取客户端数据失败: {client_result['error']}",
                    "suggestions": client_result.get("suggestions", []),
                    "component": "clientData"
                }
            
            access_token = access_token or client_result.get("accessToken")
            userid = userid or client_result.get("userid")
        
        if not access_token or not userid:
            return {
                "error": "缺少必要的访问token或用户ID",
                "suggestions": [
                    "确保已在Cursor中登录账户",
                    "检查客户端数据是否完整"
                ]
            }
        
        #
        # ========================================
        # 无头模式逻辑 - 暂时注释掉
        # ========================================
        # 原因：无头模式实现存在问题，需要完善后再启用
        # 恢复方法：取消下面的注释，并确保 DeepTokenManager.get_deep_token_headless 方法正常工作
        # ========================================
        #
        # if headless:
        #     # 无头模式：使用Python脚本获取深度token
        #     return DeepTokenManager.get_deep_token_headless(access_token, userid)
        # else:

        # 暂时强制使用浏览器模式
        if True:  # 原来是 if not headless，现在强制进入浏览器模式
            # 非无头模式：返回客户端token，让插件处理
            # 在非无头模式下，插件会使用浏览器打开深度登录页面
            created_time = datetime.now()

            return {
                "success": True,
                "accessToken": access_token,
                "userid": userid,
                "WorkosCursorSessionToken": f"{userid}%3A%3A{access_token}",
                "createdTime": created_time.isoformat(),
                "tokenType": "client",
                "needBrowserAction": True,  # 标识需要浏览器操作
                "deepLoginUrl": f"https://www.cursor.com/cn/loginDeepControl"
            }


class GetClientCurrentDataHandler(BaseActionHandler):
    """获取客户端当前数据处理器"""

    def handle(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        获取客户端当前数据
        
        params可包含:
        - mode: str, 获取模式 ('client' | 'deep_headless' | 'deep_browser'), 默认'client'
        """
        mode = params.get("mode", "client")
        
        # 首先获取基本的客户端数据
        token_result = CursorDataManager.read_access_token()
        scope_result = CursorDataManager.read_scope_json()

        # 检查token获取结果
        if "error" in token_result:
            return {
                "error": f"获取AccessToken失败: {token_result['error']}",
                "suggestions": token_result.get("suggestions", []),
                "component": "accessToken",
                "details": token_result
            }

        # 检查scope获取结果
        if "error" in scope_result:
            return {
                "error": f"获取用户信息失败: {scope_result['error']}",
                "suggestions": scope_result.get("suggestions", []),
                "component": "scopeData",
                "details": scope_result
            }

        # 验证数据完整性
        access_token = token_result.get("accessToken")
        email = scope_result.get("email")
        userid = scope_result.get("userid")

        if not access_token:
            return {
                "error": "AccessToken为空",
                "suggestions": [
                    "确保已在Cursor中登录账户",
                    "尝试重新登录Cursor",
                    "检查网络连接是否正常"
                ],
                "component": "accessToken"
            }

        if not email:
            return {
                "error": "邮箱信息为空",
                "suggestions": [
                    "确保使用邮箱登录Cursor",
                    "检查账户信息是否完整"
                ],
                "component": "email"
            }

        if not userid:
            return {
                "error": "用户ID为空",
                "suggestions": [
                    "用户ID格式可能异常",
                    "尝试重新登录Cursor账户"
                ],
                "component": "userid"
            }

        # 根据模式处理
        if mode == "client":
            # 返回客户端token（不预设有效期）
            created_time = datetime.now()
            
            return {
                "accessToken": access_token,
                "email": email,
                "userid": userid,
                "WorkosCursorSessionToken": f"{userid}%3A%3A{access_token}",
                "createdTime": created_time.isoformat(),
                "tokenType": "client",
                "success": True
            }
        #
        # ========================================
        # 无头模式逻辑 - 暂时注释掉
        # ========================================
        # 原因：无头模式实现存在问题，需要完善后再启用
        # 恢复方法：取消下面的注释，并确保 DeepTokenManager.get_deep_token_headless 方法正常工作
        # 相关方法：DeepTokenManager.get_deep_token_headless
        # ========================================
        #
        # elif mode == "deep_headless":
        #     # 无头模式获取深度token
        #     deep_result = DeepTokenManager.get_deep_token_headless(access_token, userid)
        #     if deep_result.get("success"):
        #         # 添加email信息
        #         deep_result["email"] = email
        #     return deep_result
        elif mode == "deep_browser":
            # 返回客户端数据，标识需要浏览器操作
            created_time = datetime.now()
            
            return {
                "accessToken": access_token,
                "email": email,
                "userid": userid,
                "WorkosCursorSessionToken": f"{userid}%3A%3A{access_token}",
                "createdTime": created_time.isoformat(),
                "tokenType": "client",
                "needBrowserAction": True,
                "deepLoginUrl": f"https://www.cursor.com/cn/loginDeepControl",
                "success": True
            }
        else:
            return {
                "error": f"不支持的模式: {mode}",
                "suggestions": [
                    "支持的模式: 'client', 'deep_headless', 'deep_browser'"
                ]
            }


class TestConnectionHandler(BaseActionHandler):
    """测试连接处理器 - 专门用于Chrome扩展连接测试"""
    
    def handle(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        测试原生主机连接
        
        Args:
            params: 参数字典，支持以下参数:
                - detailed (bool): 是否返回详细信息，默认False
        
        Returns:
            Dict[str, Any]: 测试结果
        """
        import platform
        import sys
        from datetime import datetime
        
        try:
            detailed = params.get("detailed", False)
            
            result = {
                "success": True,
                "message": "原生主机连接测试成功",
                "timestamp": datetime.now().isoformat(),
                "version": "1.0.0",
                "status": "connected"
            }
            
            if detailed:
                result.update({
                    "system": {
                        "platform": platform.system(),
                        "python_version": sys.version.split()[0],
                        "script_path": __file__,
                        "nativemessaging_available": NATIVEMESSAGING_AVAILABLE
                    },
                    "available_actions": [
                        "testConnection",
                        "getAccessToken",
                        "getScopeData",
                        "getClientCurrentData",
                        "getDeepToken"
                    ],
                    "capabilities": {
                        "client_token": True,
                        "deep_token": True,
                        "cursor_data": True,
                        "enhanced_messaging": NATIVEMESSAGING_AVAILABLE
                    }
                })
            
            return result
            
        except Exception as e:
            return {
                "success": False,
                "error": f"测试连接失败: {str(e)}",
                "timestamp": datetime.now().isoformat(),
                "status": "error"
            }


class ActionRegistry:
    """Action注册表"""
    
    def __init__(self):
        self._handlers: Dict[str, BaseActionHandler] = {}
    
    def register(self, action: str, handler: BaseActionHandler) -> None:
        """注册action处理器"""
        self._handlers[action] = handler
    
    def get_handler(self, action: str) -> Optional[BaseActionHandler]:
        """获取action处理器"""
        return self._handlers.get(action)
    
    def get_available_actions(self) -> list:
        """获取所有可用的action"""
        return list(self._handlers.keys())


class NativeHostServer:
    """原生主机服务器"""

    def __init__(self):
        self.registry = ActionRegistry()
        self._register_default_handlers()
        self.use_nativemessaging = NATIVEMESSAGING_AVAILABLE

    def _register_default_handlers(self):
        """注册默认的处理器"""
        self.registry.register("testConnection", TestConnectionHandler())
        self.registry.register("getAccessToken", GetAccessTokenHandler())
        self.registry.register("getScopeData", GetScopeDataHandler())
        self.registry.register("getClientCurrentData", GetClientCurrentDataHandler())
        self.registry.register("getDeepToken", GetDeepTokenHandler())

    def add_handler(self, action: str, handler: BaseActionHandler) -> None:
        """添加新的action处理器"""
        self.registry.register(action, handler)

    def get_message(self) -> Dict[str, Any]:
        """从Chrome读取消息"""
        if self.use_nativemessaging:
            # 使用 nativemessaging 库
            return nativemessaging.get_message()
        else:
            # 回退到手动实现
            raw_length = sys.stdin.buffer.read(4)
            if len(raw_length) == 0:
                sys.exit(0)
            message_length = struct.unpack('@I', raw_length)[0]
            message = sys.stdin.buffer.read(message_length).decode('utf-8')
            return json.loads(message)

    def send_message(self, message: Dict[str, Any]) -> None:
        """发送消息到Chrome"""
        if self.use_nativemessaging:
            # 使用 nativemessaging 库
            encoded_message = nativemessaging.encode_message(message)
            nativemessaging.send_message(encoded_message)
        else:
            # 回退到手动实现
            encoded_content = json.dumps(message).encode('utf-8')
            encoded_length = struct.pack('@I', len(encoded_content))
            sys.stdout.buffer.write(encoded_length)
            sys.stdout.buffer.write(encoded_content)
            sys.stdout.buffer.flush()
    
    def handle_request(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """处理请求"""
        action = message.get("action")
        params = message.get("params", {})
        
        if not action:
            return {"error": "缺少action参数"}
        
        handler = self.registry.get_handler(action)
        if not handler:
            available_actions = self.registry.get_available_actions()
            return {
                "error": f"未知操作: {action}",
                "available_actions": available_actions
            }
        
        try:
            return handler.handle(params)
        except Exception as e:
            return {"error": f"处理action '{action}' 时发生错误: {str(e)}"}
    
    def run(self) -> None:
        """运行服务器"""
        try:
            # 添加调试日志
            self.log_debug(f"原生主机启动 (使用nativemessaging: {self.use_nativemessaging})")

            # get_message方法已经处理了nativemessaging的选择逻辑
            self.log_debug(f"使用{'nativemessaging库' if self.use_nativemessaging else '手动实现'}处理消息")
            message = self.get_message()

            self.log_debug(f"收到消息: {message}")

            response = self.handle_request(message)
            self.log_debug(f"生成响应: {response}")

            self.send_message(response)
            self.log_debug("响应已发送")
        except Exception as e:
            error_response = {"error": f"处理请求时发生错误: {str(e)}"}
            self.log_debug(f"发生错误: {str(e)}")
            self.send_message(error_response)
    
    @staticmethod
    def log_debug(message: str) -> None:
        """记录调试信息到文件（仅在需要时启用）"""
        try:
            # 检查是否启用调试模式
            debug_file = "/tmp/cursor_native_host_chrome.log"
            if os.getenv("CURSOR_DEBUG") == "1":
                with open(debug_file, "a", encoding="utf-8") as f:
                    from datetime import datetime
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"[{timestamp}] {message}\n")
                    f.flush()
        except:
            pass  # 忽略日志记录错误


def main():
    """主函数"""
    import sys
    
    # 检查是否有命令行参数
    if len(sys.argv) > 1:
        if sys.argv[1] == "test":
            # 测试模式
            test_native_host()
            return
        elif sys.argv[1] == "help":
            # 帮助信息
            print_help()
            return
    
    # 正常的原生主机模式
    server = NativeHostServer()
    server.run()


def test_native_host():
    """测试原生主机功能"""
    print("🧪 测试原生主机功能...")
    
    try:
        # 测试基本功能
        server = NativeHostServer()
        
        # 测试可用actions
        available_actions = server.registry.get_available_actions()
        print(f"📋 可用actions: {available_actions}")
        
        # 测试getClientCurrentData
        print("\n🔍 测试getClientCurrentData...")
        test_message = {"action": "getClientCurrentData", "params": {"mode": "client"}}
        response = server.handle_request(test_message)
        
        if "error" in response:
            print(f"❌ 测试失败: {response['error']}")
            if "suggestions" in response:
                print("💡 建议:")
                for suggestion in response["suggestions"]:
                    print(f"  • {suggestion}")
        else:
            print("✅ getClientCurrentData测试成功")
            print(f"📧 邮箱: {response.get('email', '未知')}")
            print(f"👤 用户ID: {response.get('userid', '未知')}")
            print(f"🔑 Token类型: {response.get('tokenType', '未知')}")
        
        if "error" in response:
            print(f"⚠️ 深度Token测试: {response['error']}")
        else:
            print("✅ 深度Token配置正常")
            print(f"🔑 Token类型: {response.get('tokenType', '未知')}")
        
        print("\n✅ 原生主机功能测试完成！")
        
    except Exception as e:
        print(f"❌ 测试过程中发生错误: {str(e)}")
        import traceback
        traceback.print_exc()


def print_help():
    """打印帮助信息"""
    print("""
🔧 Cursor Client2Login 原生主机程序

用法:
  python3 native_host.py           # 正常运行模式（由Chrome调用）
  python3 native_host.py test      # 测试模式
  python3 native_host.py help      # 显示此帮助信息

测试模式:
  测试原生主机的各项功能，包括:
  - 基本连接测试
  - 客户端数据获取
  - 深度Token功能测试

注意:
  - 正常情况下，此程序由Chrome浏览器自动调用
  - 直接运行时，程序会等待来自stdin的二进制消息
  - 使用 test 参数可以进行功能测试而不需要Chrome连接
""")


if __name__ == "__main__":
    main()