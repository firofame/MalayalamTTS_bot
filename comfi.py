import subprocess

import modal

image = (
    modal.Image.debian_slim(python_version="3.13")
    .apt_install("git")
    .uv_pip_install("comfy-cli")
    .run_commands("comfy --skip-prompt install --nvidia")
)

vol = modal.Volume.from_name("hf-hub-cache", create_if_missing=True)


def hf_download():
    from huggingface_hub import hf_hub_download

    downloads = [
        # Qwen Image Edit models
        (
            "Comfy-Org/Qwen-Image-Edit_ComfyUI",
            "split_files/diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors",
            "/root/comfy/ComfyUI/models/diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors",
        ),
        (
            "lightx2v/Qwen-Image-Lightning",
            "Qwen-Image-Edit-2509/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors",
            "/root/comfy/ComfyUI/models/loras/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors",
        ),
        (
            "Comfy-Org/Qwen-Image_ComfyUI",
            "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
            "/root/comfy/ComfyUI/models/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
        ),
        (
            "Comfy-Org/Qwen-Image_ComfyUI",
            "split_files/vae/qwen_image_vae.safetensors",
            "/root/comfy/ComfyUI/models/vae/qwen_image_vae.safetensors",
        ),
        # Z-Image Turbo models
        (
            "Comfy-Org/z_image_turbo",
            "split_files/diffusion_models/z_image_turbo_bf16.safetensors",
            "/root/comfy/ComfyUI/models/diffusion_models/z_image_turbo_bf16.safetensors",
        ),
        (
            "tarn59/pixel_art_style_lora_z_image_turbo",
            "pixel_art_style_z_image_turbo.safetensors",
            "/root/comfy/ComfyUI/models/loras/pixel_art_style_z_image_turbo.safetensors",
        ),
        (
            "Comfy-Org/z_image_turbo",
            "split_files/text_encoders/qwen_3_4b.safetensors",
            "/root/comfy/ComfyUI/models/text_encoders/qwen_3_4b.safetensors",
        ),
        (
            "Comfy-Org/z_image_turbo",
            "split_files/vae/ae.safetensors",
            "/root/comfy/ComfyUI/models/vae/ae.safetensors",
        ),
    ]

    for repo_id, filename, dest in downloads:
        local_path = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            cache_dir="/cache",
        )
        subprocess.run(
            f"mkdir -p $(dirname {dest}) && ln -sf {local_path} {dest}",
            shell=True,
            check=True,
        )


image = (
    image.uv_pip_install("huggingface-hub")
    .env({"HF_XET_HIGH_PERFORMANCE": "1"})
    .run_function(
        hf_download,
        volumes={"/cache": vol},
        secrets=[modal.Secret.from_name("huggingface-secret")],
    )
)

app = modal.App(name="comfy-app", image=image)


@app.function(
    max_containers=1,
    gpu="L40s",
    volumes={"/cache": vol},
    timeout=3600,
)
@modal.concurrent(max_inputs=10)
@modal.web_server(8000, startup_timeout=120)
def ui():
    import subprocess

    subprocess.Popen("comfy launch -- --listen 0.0.0.0 --port 8000", shell=True)
