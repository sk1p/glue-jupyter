import numpy as np
import bqplot
from ipyastroimage.astroimage import AstroImage
import ipywidgets as widgets
import ipywidgets.widgets.trait_types as tt
from IPython.display import display

from glue.core.data import Subset
from glue.viewers.scatter.state import ScatterLayerState
from glue.core.exceptions import IncompatibleAttribute
from glue_jupyter.compat import LayerArtist

from ...link import link, dlink, calculation, link_component_id_to_select_widget, on_change
from ...utils import colormap_to_hexlist, debounced, float_or_none
from ...widgets import LinkedDropdown
import glue_jupyter.widgets
from glue.viewers.matplotlib.state import (MatplotlibDataViewerState,
                                           MatplotlibLayerState,
                                           DeferredDrawCallbackProperty as DDCProperty,
                                           DeferredDrawSelectionCallbackProperty as DDSCProperty)

# FIXME: monkey patch ipywidget to accept anything
tt.Color.validate = lambda self, obj, value: value

class BqplotScatterLayerState(ScatterLayerState):
    bins = DDCProperty(128, docstring='The number of bins in each dimension for the density map')


class BqplotScatterLayerArtist(LayerArtist):
    _layer_state_cls = BqplotScatterLayerState

    def __init__(self, view, viewer_state, layer_state=None, layer=None):

        super(BqplotScatterLayerArtist, self).__init__(viewer_state,
                                                       layer_state=layer_state, layer=layer)

        self.view = view

        self.scale_size = bqplot.LinearScale()
        self.scale_color = bqplot.ColorScale()
        self.scale_size_quiver = bqplot.LinearScale(min=0, max=1)
        self.scale_rotation = bqplot.LinearScale(min=0, max=1)
        self.scales = dict(self.view.scales, size=self.scale_size, rotation=self.scale_rotation, color=self.scale_color)
        self.scale_image = bqplot.ColorScale()
        self.scales_quiver = dict(self.view.scales, size=self.scale_size_quiver, rotation=self.scale_rotation)
        self.scales_image  = dict(self.view.scales, image=self.scale_image)
        self.scatter = bqplot.ScatterGL(scales=self.scales, x=[0, 1], y=[0, 1])
        self.quiver = bqplot.ScatterGL(scales=self.scales_quiver, x=[0, 1], y=[0, 1], visible=False, marker='arrow')

        self.counts = None
        self.image = AstroImage(scales=self.scales_image)
        on_change([(self.state, 'density_map')])(self._on_change_density_map)
        on_change([(self.state, 'bins')])(self._update_scatter)
        self._viewer_state.add_global_callback(self._update_scatter)

        self.view.figure.marks = list(self.view.figure.marks) + [self.image, self.scatter, self.quiver ]
        link((self.state, 'color'), (self.scatter, 'colors'), lambda x: [x], lambda x: x[0])
        link((self.state, 'color'), (self.quiver, 'colors'), lambda x: [x], lambda x: x[0])
        self.scatter.observe(self._workaround_unselected_style, 'colors')
        self.quiver.observe(self._workaround_unselected_style, 'colors')

        on_change([(self.state, 'cmap_mode', 'cmap_att')])(self._on_change_cmap_mode_or_att)
        on_change([(self.state, 'cmap')])(self._on_change_cmap)
        link((self.state, 'cmap_vmin'), (self.scale_color, 'min'), float_or_none)
        link((self.state, 'cmap_vmax'), (self.scale_color, 'max'), float_or_none)

        on_change([(self.state, 'size', 'size_scaling', 'size_mode', 'size_vmin', 'size_vmax')])(self._update_size)

        viewer_state.add_callback('x_att', self._update_xy_att)
        viewer_state.add_callback('y_att', self._update_xy_att)
        self._update_size()
        # set initial values for the colormap
        self._on_change_cmap()

    def _update_xy_att(self, *args):
        self.update()

    def _on_change_cmap_mode_or_att(self, ignore=None):
        if self.state.cmap_mode == 'Linear':
            self.scatter.color = self.layer.data[self.state.cmap_att].astype(np.float32)
        else:
            self.scatter.color = None

    def _on_change_cmap(self, ignore=None):
        cmap = self.state.cmap
        colors = colormap_to_hexlist(cmap)
        self.scale_color.colors = colors

    def _on_change_density_map(self):
        self.image.visible = self.state.density_map
        self.scatter.visible = not self.state.density_map
        self.quiver.visible = not self.state.density_map
        self._update_scatter()

    def redraw(self):
        self.update()

    def _workaround_unselected_style(self, change=None):
        # see https://github.com/bloomberg/bqplot/issues/606
        if isinstance(self.layer, Subset):
            self.scatter.unselected_style = {'fill': 'white', 'stroke': 'none'}
            self.scatter.unselected_style = {'fill': 'none', 'stroke': 'none'}
            self.quiver.unselected_style = {'fill': 'white', 'stroke': 'none'}
            self.quiver.unselected_style = {'fill': 'none', 'stroke': 'none'}

    @debounced(method=True)
    def update_histogram(self):
        if isinstance(self.layer, Subset):
            data = self.layer.data
            subset_state = self.layer.subset_state
        else:
            data = self.layer
            subset_state = None
        if self.state.density_map:
            bins = [self.state.bins, self.state.bins]
            range_x = [self.view.scale_x.min, self.view.scale_x.max]
            range_y = [self.view.scale_y.min, self.view.scale_y.max]
            range = [range_x, range_y]
            self.counts = data.compute_histogram([self._viewer_state.y_att, self._viewer_state.x_att], subset_state=subset_state, bins=bins, range=range)
            self.scale_image.min = 0
            self.scale_image.max = np.nanmax(self.counts)
            with self.image.hold_sync():
                self.image.x = range_x
                self.image.y = range_y
                self.image.image = self.counts.T.copy(np.float32)

    def _update_scatter(self, **changes):
        self.update_histogram()
        self.update()

    def update(self):

        if self.state.density_map:
            pass
        else:
            self.scatter.x = self.layer.data[self._viewer_state.x_att].astype(np.float32)
            self.scatter.y = self.layer.data[self._viewer_state.y_att].astype(np.float32)
            self.quiver.x = self.layer.data[self._viewer_state.x_att].astype(np.float32)
            self.quiver.y = self.layer.data[self._viewer_state.y_att].astype(np.float32)

        if isinstance(self.layer, Subset):

            try:
                mask = self.layer.to_mask()
            except IncompatibleAttribute:
                self.disable("Could not compute subset")
                self._clear_selection()
                return

            selected_indices = np.nonzero(mask)[0].tolist()

            self.scatter.selected = selected_indices
            self.scatter.selected_style = {}
            self.scatter.unselected_style = {'fill': 'none', 'stroke': 'none'}
            self.quiver.selected = selected_indices
            self.quiver.selected_style = {}
            self.quiver.unselected_style = {'fill': 'none', 'stroke': 'none'}

        else:
            self._clear_selection()

    def _clear_selection(self):
        self.scatter.selected = None
        self.scatter.selected_style = {}
        self.scatter.unselected_style = {}
        self.quiver.selected = None
        self.quiver.selected_style = {}
        self.quiver.unselected_style = {}


    def _update_quiver(self):
        if not self.state.vector_visible:
            return
        size = 50
        scale = 1
        self.quiver.default_size = int(size * scale * 4)
        vx = self.layer.data[self.state.vx_att]
        vy = self.layer.data[self.state.vy_att]
        length = np.sqrt(vx**2 + vy**2)
        self.scale_size_quiver.min = np.nanmin(length)
        self.scale_size_quiver.max = np.nanmax(length)
        self.quiver.size = length
        angle = np.arctan2(vy, vx)
        self.scale_rotation.min = -np.pi
        self.scale_rotation.max = np.pi
        self.quiver.rotation = angle

    def create_widgets(self):
        self.widget_visible = widgets.Checkbox(description='visible', value=self.state.visible)
        link((self.state, 'visible'), (self.widget_visible, 'value'))
        link((self.state, 'visible'), (self.scatter, 'visible'))
        return widgets.VBox([self.widget_visible])

    def _update_size(self):
        size = self.state.size
        scale = self.state.size_scaling
        if self.state.size_mode == 'Linear':
            self.scatter.default_size = int(scale * 25) # *50 seems to give similar sizes as the Qt Glue
            self.scatter.size = self.layer.data[self.state.size_att]
            self.scale_size.min = float_or_none(self.state.size_vmin)
            self.scale_size.max = float_or_none(self.state.size_vmax)
            self._workaround_unselected_style()
        else:
            self.scatter.default_size = int(size * scale)
            self.scatter.size = None
            self.scale_size.min = 0
            self.scale_size.max = 1

    def create_widgets(self):
        self.widget_visible = widgets.Checkbox(description='visible', value=self.state.visible)
        link((self.state, 'visible'), (self.widget_visible, 'value'))
        link((self.state, 'visible'), (self.scatter, 'visible'))

        self.widget_opacity = widgets.FloatSlider(min=0, max=1, step=0.01, value=self.state.alpha, description='opacity')
        link((self.state, 'alpha'), (self.widget_opacity, 'value'))
        link((self.state, 'alpha'), (self.scatter, 'default_opacities'), lambda x: [x], lambda x: x[0])
        link((self.state, 'alpha'), (self.quiver, 'default_opacities'), lambda x: [x], lambda x: x[0])

        self.widget_color = glue_jupyter.widgets.Color(state=self.state)
        self.widget_size = glue_jupyter.widgets.Size(state=self.state)

        self.widget_vector = widgets.Checkbox(description='show vectors', value=self.state.vector_visible)
        helper = self.state.vx_att_helper

        self.widget_vector_x = LinkedDropdown(self.state, 'vx_att', ui_name='vx', label='vx attribute')
        self.widget_vector_y = LinkedDropdown(self.state, 'vy_att', ui_name='vy', label='vy attribute')
        on_change([(self.state, 'vector_visible', 'vx_att', 'vy_att')])(self._update_quiver)
        link((self.state, 'vector_visible'), (self.widget_vector, 'value'))
        link((self.state, 'vector_visible'), (self.quiver, 'visible'))
        dlink((self.widget_vector, 'value'), (self.widget_vector_x.layout, 'display'), lambda value: None if value else 'none')
        dlink((self.widget_vector, 'value'), (self.widget_vector_y.layout, 'display'), lambda value: None if value else 'none')

        self.widget_bins = widgets.IntSlider(min=0, max=1024, value=self.state.bins, description='bin count')
        link((self.state, 'bins'), (self.widget_bins, 'value'))

        return widgets.VBox([self.widget_visible, self.widget_opacity,
            self.widget_size,
            self.widget_color,
            self.widget_vector, self.widget_vector_x, self.widget_vector_y])