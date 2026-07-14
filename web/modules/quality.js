// quality.js — format/quality dropdown options, shared by the download
// form (scan-form.js) and the batch converter form (convert.js).
import { qualitySelect } from "./dom.js";

export const QUALITY_OPTIONS = {
  MP4: [
    { value: "best",  label: "Best available" },
    { value: "2160p", label: "2160p (4K)" },
    { value: "1440p", label: "1440p (2K)" },
    { value: "1080p", label: "1080p" },
    { value: "720p",  label: "720p" },
    { value: "480p",  label: "480p" },
    { value: "360p",  label: "360p" },
    { value: "240p",  label: "240p" },
  ],
  MP3: [
    { value: "192k", label: "192 kbps (standard)" },
    { value: "320k", label: "320 kbps (high)" },
    { value: "256k", label: "256 kbps" },
    { value: "128k", label: "128 kbps" },
    { value: "96k",  label: "96 kbps (small)" },
  ],
  "3GP": [
    { value: "320x240", label: "320×240 (standard)" },
    { value: "352x288", label: "352×288 (higher)" },
    { value: "176x144", label: "176×144 (smallest)" },
  ],
};

export function populateQualityOptions() {
  const fmt = document.querySelector("input[name='format']:checked")?.value || "MP4";
  const opts = QUALITY_OPTIONS[fmt] || QUALITY_OPTIONS.MP4;
  qualitySelect.innerHTML = opts.map(o => `<option value="${o.value}">${o.label}</option>`).join("");
}
document.querySelectorAll("input[name='format']").forEach(radio =>
  radio.addEventListener("change", populateQualityOptions)
);
populateQualityOptions(); // initial fill on page load
