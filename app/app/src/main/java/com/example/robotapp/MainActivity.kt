package com.example.robotapp

import android.Manifest
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.camera.core.*
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.google.accompanist.permissions.ExperimentalPermissionsApi
import com.google.accompanist.permissions.rememberMultiplePermissionsState
import kotlinx.coroutines.*
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.Socket
import java.nio.ByteBuffer
import java.nio.ByteOrder

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            RobotappTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.surface
                ) {
                    CameraStreamScreen()
                }
            }
        }
    }
}

enum class CameraType {
    CAM_HIGH,      // 俯视摄像头 (camera_id = 0)
    CAM_LEFT_WRIST // 左视摄像头 (camera_id = 1)
}

@OptIn(ExperimentalPermissionsApi::class)
@Composable
fun CameraStreamScreen() {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current

    // 权限请求
    val permissionsState = rememberMultiplePermissionsState(
        permissions = listOf(
            Manifest.permission.CAMERA
        )
    )

    // UI状态
    var pcIpAddress by remember { mutableStateOf("192.168.1.100") }
    var selectedCameraType by remember { mutableStateOf(CameraType.CAM_HIGH) }
    var isConnected by remember { mutableStateOf(false) }
    var connectionStatus by remember { mutableStateOf("未连接") }

    // TCP客户端
    var tcpClient by remember { mutableStateOf<TcpVideoClient?>(null) }
    // 图像分析器引用
    var imageAnalysisRef by remember { mutableStateOf<ImageAnalysis?>(null) }

    // 请求权限
    LaunchedEffect(Unit) {
        if (!permissionsState.allPermissionsGranted) {
            permissionsState.launchMultiplePermissionRequest()
        }
    }

    // --- 布局修改：使用 Column 分割屏幕 ---
    Column(
        modifier = Modifier
            .fillMaxSize()
            // 添加系统栏内边距，防止内容被状态栏/导航栏遮挡
            .windowInsetsPadding(WindowInsets.systemBars)
    ) {
        // 1. 顶部：摄像头预览区域
        // 使用 weight(1f) 让它占据除控制区以外的所有剩余空间
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f), // 关键：占据剩余高度

            contentAlignment = Alignment.Center
        ) {
            if (permissionsState.allPermissionsGranted) {
                CameraPreview(
                    context = context,
                    lifecycleOwner = lifecycleOwner,
                    onImageAvailable = { bitmap ->
                        if (isConnected) {
                            tcpClient?.sendFrame(bitmap)
                        }
                    },
                    onImageAnalysisCreated = { analysis ->
                        imageAnalysisRef = analysis
                    },
                    modifier = Modifier.fillMaxSize() // 填满 Box 区域
                )
            } else {
                Text(
                    text = "等待摄像头权限...",
                    color = MaterialTheme.colorScheme.surfaceVariant
                )
            }
        }

        // 2. 底部：控制面板区域
        // 使用 Surface 给控制区一个背景，使其与视频区分开
        Surface(
            modifier = Modifier.fillMaxWidth(),
            tonalElevation = 8.dp, // 稍微抬起，增加阴影层次感
            shadowElevation = 4.dp
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(16.dp)
                    .verticalScroll(rememberScrollState()), // 如果内容太多，允许内部滚动
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Text(
                    text = "机器人控制台",
                    style = MaterialTheme.typography.titleLarge
                )

                // IP地址输入
                OutlinedTextField(
                    value = pcIpAddress,
                    onValueChange = { if (!isConnected) pcIpAddress = it },
                    label = { Text("PC IP地址") },
                    placeholder = { Text("例如: 192.168.1.100") },
                    enabled = !isConnected,
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true // 设为单行，避免占用过多空间
                )

                // 摄像头类型选择
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    FilterChip(
                        selected = selectedCameraType == CameraType.CAM_HIGH,
                        onClick = { if (!isConnected) selectedCameraType = CameraType.CAM_HIGH },
                        label = { Text("俯视 (High)") },
                        enabled = !isConnected,
                        modifier = Modifier.weight(1f)
                    )
                    FilterChip(
                        selected = selectedCameraType == CameraType.CAM_LEFT_WRIST,
                        onClick = { if (!isConnected) selectedCameraType = CameraType.CAM_LEFT_WRIST },
                        label = { Text("左视图") },
                        enabled = !isConnected,
                        modifier = Modifier.weight(1f)
                    )
                }

                // 连接状态显示
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.Center,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    // 指示灯小圆点
                    Surface(
                        modifier = Modifier.size(12.dp),
                        shape = androidx.compose.foundation.shape.CircleShape,
                        color = if (isConnected) androidx.compose.ui.graphics.Color.Green else MaterialTheme.colorScheme.error
                    ) {}
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(text = "状态: $connectionStatus")
                }

                // 连接/断开按钮
                Button(
                    onClick = {
                        if (isConnected) {
                            tcpClient?.close()
                            tcpClient = null
                            imageAnalysisRef?.clearAnalyzer()
                            imageAnalysisRef = null
                            isConnected = false
                            connectionStatus = "已断开"
                        } else {
                            if (permissionsState.allPermissionsGranted) {
                                connectionStatus = "正在连接..."
                                val client = TcpVideoClient(
                                    pcIpAddress,
                                    selectedCameraType,
                                    onConnected = {
                                        isConnected = true
                                        connectionStatus = "已连接"
                                    },
                                    onError = { error ->
                                        isConnected = false
                                        connectionStatus = "错误: $error"
                                    }
                                )
                                tcpClient = client
                                client.start()
                            } else {
                                connectionStatus = "需要权限"
                            }
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = if (isConnected) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary
                    )
                ) {
                    Text(if (isConnected) "断开连接" else "开始连接")
                }
            }
        }
    }

    // 清理资源
    DisposableEffect(Unit) {
        onDispose {
            tcpClient?.close()
            imageAnalysisRef?.clearAnalyzer()
        }
    }
}

@Composable
fun CameraPreview(
    context: Context,
    lifecycleOwner: androidx.lifecycle.LifecycleOwner,
    onImageAvailable: (Bitmap) -> Unit,
    onImageAnalysisCreated: (ImageAnalysis) -> Unit,
    modifier: Modifier = Modifier
) {
    val previewView = remember { PreviewView(context) }
    
    LaunchedEffect(Unit) {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(context)
        val cameraProvider = cameraProviderFuture.get()
        
        val preview = Preview.Builder().build().also {
            it.setSurfaceProvider(previewView.surfaceProvider)
        }
        
        val imageAnalysis = ImageAnalysis.Builder()
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
            .setTargetResolution(android.util.Size(640, 480)) // 降采样
            .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
            .build()
            .also {
                it.setAnalyzer(
                    ContextCompat.getMainExecutor(context),
                    ImageAnalyzer(onImageAvailable)
                )
            }
        
        onImageAnalysisCreated(imageAnalysis)
        
        val cameraSelector = CameraSelector.DEFAULT_BACK_CAMERA
        
        try {
            cameraProvider.unbindAll()
            cameraProvider.bindToLifecycle(
                lifecycleOwner,
                cameraSelector,
                preview,
                imageAnalysis
            )
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }
    
    AndroidView(
        factory = { previewView },
        modifier = modifier
    )
}

class ImageAnalyzer(private val onImageAvailable: (Bitmap) -> Unit) : ImageAnalysis.Analyzer {
    private val scope = CoroutineScope(Dispatchers.Default + SupervisorJob())
    private var lastFrameTime = 0L
    private val targetFps = 30
    private val frameInterval = 1000L / targetFps // ~33ms per frame
    
    override fun analyze(imageProxy: ImageProxy) {
        val currentTime = System.currentTimeMillis()
        // 限制帧率
        if (currentTime - lastFrameTime < frameInterval) {
            imageProxy.close()
            return
        }
        lastFrameTime = currentTime
        
        scope.launch {
            try {
                val bitmap = imageProxy.toBitmap()
                onImageAvailable(bitmap)
            } catch (e: Exception) {
                e.printStackTrace()
            } finally {
                imageProxy.close()
            }
        }
    }
}

fun ImageProxy.toBitmap(): Bitmap {
    // RGBA_8888格式直接转换
    val buffer = planes[0].buffer
    val bytes = ByteArray(buffer.remaining())
    buffer.rewind()
    buffer.get(bytes)
    
    val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
    bitmap.copyPixelsFromBuffer(buffer.rewind() as java.nio.ByteBuffer)
    
    return bitmap
}

class TcpVideoClient(
    private val serverIp: String,
    private val cameraType: CameraType,
    private val onConnected: () -> Unit,
    private val onError: (String) -> Unit
) {
    private val serverPort = 8888
    private var socket: Socket? = null
    private var outputStream: java.io.OutputStream? = null
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private var isRunning = false
    
    fun start() {
        scope.launch {
            try {
                socket = Socket(serverIp, serverPort)
                outputStream = socket?.getOutputStream()
                
                // 发送摄像头类型标识
                val cameraId = when (cameraType) {
                    CameraType.CAM_HIGH -> 0
                    CameraType.CAM_LEFT_WRIST -> 1
                }
                outputStream?.write(byteArrayOf(cameraId.toByte()))
                
                isRunning = true
                withContext(Dispatchers.Main) {
                    onConnected()
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    onError(e.message ?: "连接失败")
                }
            }
        }
    }
    
    fun sendFrame(bitmap: Bitmap) {
        if (!isRunning || outputStream == null) return
        
        scope.launch {
            try {
                val stream = ByteArrayOutputStream()
                bitmap.compress(Bitmap.CompressFormat.JPEG, 80, stream)
                val imageBytes = stream.toByteArray()
                
                // 发送帧大小（4字节，大端序）
                val sizeBuffer = ByteBuffer.allocate(4).order(ByteOrder.BIG_ENDIAN)
                sizeBuffer.putInt(imageBytes.size)
                outputStream?.write(sizeBuffer.array())
                
                // 发送图像数据
                outputStream?.write(imageBytes)
                outputStream?.flush()
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    onError("发送失败: ${e.message}")
                }
            }
        }
    }
    
    fun close() {
        isRunning = false
        scope.launch {
            try {
                outputStream?.close()
                socket?.close()
            } catch (e: Exception) {
                e.printStackTrace()
            }
        }
    }
}
@Composable
fun RobotappTheme(
    darkTheme: Boolean = androidx.compose.foundation.isSystemInDarkTheme(),
    content: @Composable () -> Unit
) {
    val colorScheme = if (darkTheme) {
        darkColorScheme(
            primary = androidx.compose.ui.graphics.Color(0xFFBB86FC),
            secondary = androidx.compose.ui.graphics.Color(0xFF03DAC5),
            tertiary = androidx.compose.ui.graphics.Color(0xFF3700B3)
        )
    } else {
        lightColorScheme(
            primary = androidx.compose.ui.graphics.Color(0xFF6200EE),
            secondary = androidx.compose.ui.graphics.Color(0xFF03DAC5),
            tertiary = androidx.compose.ui.graphics.Color(0xFF018786)
        )
    }

    MaterialTheme(
        colorScheme = colorScheme,
        typography = Typography(),
        content = content
    )
}