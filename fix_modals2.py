"""Fix: Convert inline form panels to modal dialogs — using anchor-based offsets."""
with open('src/web/templates/index.html', 'r') as f:
    content = f.read()

# ─── Step 1: Find exact boundaries using anchor patterns ───
# CH form: starts at id="chf"
chf_start = content.find('id="chf"')
chf_flex = content.find('<div class="flex gap8">', chf_start)
chf_flex_close = content.find('</div>', chf_flex + 25)
chf_form_close = content.find('</div>', chf_flex_close + 5)
chf_end = chf_form_close + 6
chf_outer = content[chf_start:chf_end]
chf_inner_start = content.find('>', chf_start) + 1
chf_inner = content[chf_inner_start:chf_end]
print("CH: %d:%d outer_len=%d inner_len=%d" % (chf_start, chf_end, len(chf_outer), len(chf_inner)))

# CF form: starts at id="cf"
cf_start = content.find('id="cf"')
cf_flex = content.find('<div class="flex gap8">', cf_start)
cf_flex_close = content.find('</div>', cf_flex + 25)
cf_form_close = content.find('</div>', cf_flex_close + 5)
cf_end = cf_form_close + 6
cf_outer = content[cf_start:cf_end]
cf_inner_start = content.find('>', cf_start) + 1
cf_inner = content[cf_inner_start:cf_end]
print("CF: %d:%d outer_len=%d inner_len=%d" % (cf_start, cf_end, len(cf_outer), len(cf_inner)))

# RF form: starts at id="rf"
rf_start = content.find('id="rf"')
rf_flex = content.find('<div class="flex gap8">', rf_start)
rf_flex_close = content.find('</div>', rf_flex + 25)
rf_form_close = content.find('</div>', rf_flex_close + 5)
rf_end = rf_form_close + 6
rf_outer = content[rf_start:rf_end]
rf_inner_start = content.find('>', rf_start) + 1
rf_inner = content[rf_inner_start:rf_end]
print("RF: %d:%d outer_len=%d inner_len=%d" % (rf_start, rf_end, len(rf_outer), len(rf_inner)))

# Verify boundaries end with proper closing divs
print("CH outer ends:", repr(chf_outer[-20:]))
print("CF outer ends:", repr(cf_outer[-20:]))
print("RF outer ends:", repr(rf_outer[-20:]))

# ─── Step 2: Build modal HTML ───
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
        + "<button class=\"btn btn-xs btn-outline\" style=\"margin-left:auto\" onclick=\"" + close_fn + "\">X</button>"
        + "</div><div class=\"card-bd\">" + inner_html + "</div></div></div>"
    )

chf_modal = make_modal('chfModal', '&#128259; 通知渠道', chf_inner)
cf_modal = make_modal('cfModal', '&#128100; 客户', cf_inner)
rf_modal = make_modal('rfModal', '&#128203; 订阅规则', rf_inner)

# ─── Step 3: Progressive replacement with running offset ───
new_content = content
total_offset = 0

# CH first
chf_ph = '<div id="chf-placeholder" style="display:none"></div>'
old_len = chf_end - chf_start
new_len = len(chf_ph)
total_offset += new_len - old_len
new_content = new_content[:chf_start] + chf_ph + new_content[chf_end:]

# CF next (offset by CH delta)
cf_start2 = cf_start + total_offset
cf_end2 = cf_end + total_offset
cf_ph = '<div id="cf-placeholder" style="display:none"></div>'
old_len = cf_end2 - cf_start2
new_len = len(cf_ph)
total_offset += new_len - old_len
new_content = new_content[:cf_start2] + cf_ph + new_content[cf_end2:]

# RF last (offset by CH+CF delta)
rf_start2 = rf_start + total_offset
rf_end2 = rf_end + total_offset
rf_ph = '<div id="rf-placeholder" style="display:none"></div>'
old_len = rf_end2 - rf_start2
new_len = len(rf_ph)
total_offset += new_len - old_len
new_content = new_content[:rf_start2] + rf_ph + new_content[rf_end2:]

# Insert modals before </body>
body = new_content.rfind('</body>')
new_content = new_content[:body] + chf_modal + cf_modal + rf_modal + new_content[body:]

# ─── Step 4: Update show*Form functions ───
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

# Cancel buttons
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

# ─── Verify before writing ───
chf_gone = 'id="chf" class="form-panel' not in new_content
cf_gone = 'id="cf" class="form-panel' not in new_content
rf_gone = 'id="rf" class="form-panel' not in new_content
print("\nVerification:")
print("Original: %d chars, New: %d chars, Delta: %d" % (len(content), len(new_content), len(new_content)-len(content)))
print("chfModal=%d cfModal=%d rfModal=%d" % (new_content.count('chfModal'), new_content.count('cfModal'), new_content.count('rfModal')))
print("Placeholders: chf=%s cf=%s rf=%s" % ('chf-placeholder' in new_content, 'cf-placeholder' in new_content, 'rf-placeholder' in new_content))
print("Old divs gone: chf=%s cf=%s rf=%s" % (chf_gone, cf_gone, rf_gone))

# Show CF area to confirm no broken markup
cf_ph_pos = new_content.find('cf-placeholder')
if cf_ph_pos > 0:
    print("\nCF placeholder context: %s" % repr(new_content[cf_ph_pos-60:cf_ph_pos+80]))

# ─── Write ───
with open('src/web/templates/index.html', 'w') as f:
    f.write(new_content)
print("\nFile written!")
