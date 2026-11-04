"""The editor's Animation tab: animations played on a mannequin in a live preview."""
from . import preview, tabs

ID = "animation"


def view(ui, well):
    preview.draw(ui, well.pad(ui.t.pad), ID, "Pick an animation to play it here")


def register():
    tabs.register_tab(tabs.Tab(ID, "Animation", 'ARMATURE_DATA', nav_icon="Animation", group="tools", view=view))


def unregister():
    tabs.unregister_tab(ID)
