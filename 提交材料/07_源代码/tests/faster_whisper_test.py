from faster_whisper import WhisperModel

model = WhisperModel(
    model_size_or_path="./models/faster-whisper-tiny",
    device="cpu",
    compute_type="int8",
    local_files_only=True   # 强制只读取本地文件，不再调用huggingface hub联网校验！
)
print("✅本地模型加载成功")
