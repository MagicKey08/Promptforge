document.addEventListener("DOMContentLoaded", () => {
    const closeAllDropdowns = () => {
        document.querySelectorAll(".dropdown-menu").forEach(menu => {
            menu.classList.remove("show");
            menu.style.opacity = "0";
        });
        document.querySelectorAll(".product-card").forEach(card => {
            card.addEventListener("click", () => {
                const productId = card.dataset.id;
                openProductModal(productId);
            });
        });
    };

    const setupDropdown = (wrapperId, menuId) => {
        const wrapper = document.getElementById(wrapperId);
        const menu = document.getElementById(menuId);
        let timeout;

        if (wrapper && menu) {
            wrapper.addEventListener("mouseenter", () => {
                closeAllDropdowns();
                clearTimeout(timeout);
                menu.classList.add("show");
                menu.style.opacity = "1";
            });
            wrapper.addEventListener("mouseleave", () => {
                timeout = setTimeout(() => {
                    menu.classList.remove("show");
                    menu.style.opacity = "0";
                }, 250);
            });
            menu.addEventListener("mouseenter", () => clearTimeout(timeout));
            menu.addEventListener("mouseleave", () => {
                timeout = setTimeout(() => {
                    menu.classList.remove("show");
                    menu.style.opacity = "0";
                }, 250);
            });
        }
    };

    setupDropdown("userWrapper", "userDropdown");
    setupDropdown("cartWrapper", "cartDropdown");

    const themeSelect = document.getElementById("themeSelect");
    const applyTheme = theme => {
        if (theme === "light") document.body.classList.add("light");
        else if (theme === "dark") document.body.classList.remove("light");
        else document.body.classList.toggle("light", window.matchMedia("(prefers-color-scheme: light)").matches);
    };

    const currentTheme = localStorage.getItem("theme") || "system";
    applyTheme(currentTheme);
    if (themeSelect) {
        themeSelect.value = currentTheme;
        themeSelect.addEventListener("change", () => {
            localStorage.setItem("theme", themeSelect.value);
            applyTheme(themeSelect.value);
        });
    }

    const passwordInput = document.querySelector("input[name='new_password']");
    if (passwordInput) {
        passwordInput.addEventListener("input", () => {
            const val = passwordInput.value;
            let score = 0;
            if (val.length >= 8) score++;
            if (/[A-Z]/.test(val)) score++;
            if (/[0-9]/.test(val)) score++;
            if (/[^A-Za-z0-9]/.test(val)) score++;
            const colors = ["#dc3545", "#ffc107", "#28a745", "#28a745"];
            passwordInput.style.borderColor = colors[score] || "#ccc";
        });
    }

    document.querySelectorAll("form").forEach(form => {
        form.addEventListener("submit", () => {
            const btn = form.querySelector("button[type='submit']");
            if (btn) {
                btn.disabled = true;
                btn.textContent = "⏳ Wird verarbeitet...";
            }
        });
    });

    const mobileToggle = document.getElementById("mobileMenuToggle");
    const mobileMenu = document.getElementById("mobileMenu");
    if (mobileToggle && mobileMenu) {
        mobileToggle.addEventListener("click", () => {
            const expanded = mobileToggle.getAttribute("aria-expanded") === "true";
            mobileToggle.setAttribute("aria-expanded", !expanded);
            mobileMenu.classList.toggle("show");
        });

        document.addEventListener("click", e => {
            if (!mobileMenu.contains(e.target) && !mobileToggle.contains(e.target)) {
                mobileMenu.classList.remove("show");
                mobileToggle.setAttribute("aria-expanded", "false");
            }
        });
    }

    document.querySelectorAll(".toast").forEach(toast => {
        toast.classList.add("show");
        setTimeout(() => toast.classList.remove("show"), 4000);
    });

    // Modal Vorschau
    window.openProductModal = async (productId) => {
        const modal = document.getElementById("productModal");
        const image = document.getElementById("modalImage");
        const title = document.getElementById("modalTitle");
        const description = document.getElementById("modalDescription");
        const price = document.getElementById("modalPrice");
        const form = document.getElementById("addToCartForm");
        const buyNow = document.getElementById("buyNowLink");

        const res = await fetch(`/product-info/${productId}`);
        const data = await res.json();

        image.src = `/static/preview_images/${data.preview_image}`;
        title.textContent = data.title;
        description.textContent = data.description;
        price.textContent = `💶 ${(data.price / 100).toFixed(2)} €`;
        form.action = `/add-to-cart/${productId}`;
        buyNow.href = `/checkout/${productId}`;

        modal.classList.add("show");
    };

    window.closeProductModal = () => {
        document.getElementById("productModal").classList.remove("show");
    };

    document.querySelectorAll(".product-card").forEach(card => {
        card.addEventListener("click", () => {
            const productId = card.dataset.id;
            openProductModal(productId);
        });
    });

    // Drag & Drop
    const dropZone = document.getElementById("dropZone");
    const imageInput = document.getElementById("imageInput");
    const imagePreview = document.getElementById("imagePreview");

    if (dropZone && imageInput && imagePreview) {
        dropZone.addEventListener("click", () => imageInput.click());
        imageInput.addEventListener("change", (e) => {
            const file = e.target.files[0];
            if (file && file.type.startsWith("image")) {
                const reader = new FileReader();
                reader.onload = () => {
                    imagePreview.src = reader.result;
                    imagePreview.style.display = "block";
                };
                reader.readAsDataURL(file);
            }
        });
        dropZone.addEventListener("dragover", e => {
            e.preventDefault();
            dropZone.classList.add("highlight");
        });
        dropZone.addEventListener("dragleave", () => dropZone.classList.remove("highlight"));
        dropZone.addEventListener("drop", e => {
            e.preventDefault();
            dropZone.classList.remove("highlight");
            imageInput.files = e.dataTransfer.files;
            imageInput.dispatchEvent(new Event("change"));
        });
    }
});