# To be placed in a Parameter Execute DAT - starts/stops the fal connection.
#
# Setup:
#   1. Right-click the fal_manager COMP > Customize Component...
#   2. Add a Toggle parameter named "Connect"
#   3. Add a Parameter Execute DAT, point it at the fal_manager COMP,
#      enable "On Value Change", and paste this code into it
#
# Optional: bind a Text DAT or label to  op('fal_manager').ext.FalExt.get_status()
# to display 'live' / 'connecting' / 'stopped'.

def onValueChange(par, val, prev):
    ext = op('fal_manager').ext.FalExt
    if val:
        ext.start()
    else:
        ext.stop()
