"""Fix: Convert inline form panels to modal dialogs.
Precisely extracts form inner HTML and replaces with modals.
"""
with open('/root/nsfocus-monitor/src/web/templates/index.html', 'r') as f:
    content = f.read()

# ─── Exact boundaries (confirmed by char inspection) ───
# CH: outer 95488-96119, inner 95528-96113
# CF: outer 101718-102804, inner 101757-102798
# RF: outer 108468-111444, inner 108507-111438

CH_OUTER_START, CH_OUTER_END = 95488, 96119
CH_INNER_START, CH_INNER_END = 95528, 96113
CF_OUTER_START, CF_OUTER_END = 101718, 102804
CF_INNER_START, CF_INNER_END = 101757, 102798
RF_OUTER_START, RF_OUTER_END = 108468, 111444
RF_INNER_START, RF_INNER_END = 108507, 111438

# Extract outer HTML (full div including wrapper)
chf_outer = content[CH_OUTER_START:CH_OUTER_END]
cf_outer = content[CF_OUTER_START:CF_OUTER_END]
rf_outer = content[RF_OUTER_START:RF_OUTER_END]

# Extract inner HTML (content inside the form-panel div, no wrapper)
chf_inner = content[CH_INNER_START:CH_INNER_END]
cf_inner = content[CF_INNER_START:CF_INNER_END]
rf_inner = content[RF_INNER_START:RF_INNER_END]

print(f"CH outer={len(chf_outer)}, inner={len(chf_inner)}")
print(f"CF outer={len(cf_outer)}, inner={len(cf_inner)}")
print(f"RF outer={len(rf_outer)}, inner={len(rf_inner)}")

# ─── Build modal HTML ───
# Modal is plain HTML (not inside template string), uses standard double-quote attributes
# The form inner HTML is embedded inside the modal's card-bd div
# Close button inside modal uses onclick with modal ID
# Clicking backdrop also closes modal

MODAL_STYLE = ('style="position:fixed;top:0;left:0;width:100%;height:100%;'
               'background:rgba(0,0,0,.45);z-index:9999;display:none;'
               'align-items:center;justify-content:center"')

def make_modal(modal_id, title, inner_html):
    close_fn = "document.getElementById('" + modal_id + "').style.display='none'"
    return (
        "<div id=\"" + modal_id + "\" " + MODAL_STYLE
        + " onclick=\"if(event.target===this)" + close_fn + "\">"
        "<div class=\"card\" style=\"width:560px;max-width:95vw;max-height:90vh;overflow-y:auto\">"
        "<div class=\"card-hd\" style=\"display:flex;align-items:center;gap:8px\">" + title
        + "<button class=\"btn btn-xs btn-outline\" style=\"margin-left:auto\" onclick=\"" + close_fn + "\">×</button>"
        + "</div><div class=\"card-bd\">" + inner_html + "</div></div></div>"
    )

chf_modal = make_modal('chfModal', '📡 通知渠道', chf_inner)
cf_modal = make_modal('cfModal', '👥 客户', cf_inner)
rf_modal = make_modal('rfModal', '📋 订阅规则', rf_inner)

# ─── Build new content ───
new_content = content

# Replace each form div with a tiny placeholder
chf_ph = '<div id="chf-placeholder" style="display:none"></div>'
cf_ph = '<div id="cf-placeholder" style="display:none"></div>'
rf_ph = '<div id="rf-placeholder" style="display:none"></div>'

# Replace CH form (first, no offset needed)
new_content = new_content[:CH_OUTER_START] + chf_ph + new_content[CH_OUTER_END:]
offset1 = len(chf_ph) - len(chf_outer)

# Replace CF form (offset adjusted by CH delta)
cf_start = CF_OUTER_START + offset1
cf_end = CF_OUTER_END + offset1
new_content = new_content[:cf_start] + cf_ph + new_content[cf_end:]
offset2 = len(cf_ph) - len(cf_outer)

# Replace RF form (offset adjusted by CH+CF delta)
rf_start = RF_OUTER_START + offset1 + offset2
rf_end = RF_OUTER_END + offset1 + offset2
new_content = new_content[:rf_start] + rf_ph + new_content[rf_end:]

# Insert modal containers before </body>
body_pos = new_content.rfind('</body>')
new_content = new_content[:body_pos] + chf_modal + cf_modal + rf_modal + new_content[body_pos:]

# ─── Update show*Form functions to open modal ───
# .classList.remove('hidden') on form div -> style.display='flex' on modal
new_content = new_content.replace(
    "document.getElementById('chf').classList.remove('hidden')",
    "document.getElementById('chfModal').style.display='flex'"
)
new_content = new_content.replace(
    "document.getElementById('cf').classList.remove('hidden')",
    "document.getElementById('cfModal').style.display='flex'"
)
new_content = new_content.replace(
    "document.getElementById('rf').classList.remove('hidden')",
    "document.getElementById('rfModal').style.display='flex'"
)

# ─── Update cancel buttons to close modal ───
# CH cancel (with editingChId)
new_content = new_content.replace(
    "document.getElementById('chf').classList.add('hidden');editingChId=0",
    "document.getElementById('chfModal').style.display='none';editingChId=0"
)
new_content = new_content.replace(
    "document.getElementById('chf').classList.add('hidden')",
    "document.getElementById('chfModal').style.display='none'"
)
new_content = new_content.replace(
    "document.getElementById('cf').classList.add('hidden')",
    "document.getElementById('cfModal').style.display='none'"
)
new_content = new_content.replace(
    "document.getElementById('rf').classList.add('hidden')",
    "document.getElementById('rfModal').style.display='none'"
)

# ─── Verify ───
print("\nVerification:")
print(f"Original: {len(content)} chars, New: {len(new_content)} chars, Delta: {len(new_content) - len(content)}")

# Check no broken form-panel references remain
print("chfModal count:", new_content.count('chfModal'))
print("cfModal count:", new_content.count('cfModal'))
print("rfModal count:", new_content.count('rfModal'))

# Check old form-panel hidden divs are gone
print("chf div gone:", 'id="chf" class="form-panel' not in new_content)
print("cf div gone:", 'id="cf" class="form-panel' not in new_content)
print("rf div gone:", 'id="rf" class="form-panel' not in new_content)

# Check placeholders exist
print("chf-placeholder:", 'chf-placeholder' in new_content)
print("cf-placeholder:", 'cf-placeholder' in new_content)
print("rf-placeholder:", 'rf-placeholder' in new_content)

# Check modal HTML is valid (at least has the three </div> closing sequence)
triple_count = new_content.count('</div></div></div>')
print(f"Triple </div> count: {triple_count} (should be >= 3)")

# Verify showChForm
idx = new_content.find("function showChForm")
print("\nshowChForm:", new_content[idx:idx+200])

# ─── Write ───
with open('/root/nsfocus-monitor/src/web/templates/index.html', 'w') as f:
    f.write(new_content)
print("\nFile written successfully!")
