from django.contrib import admin, messages
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import (Aggiudicazione, Asta, Incasso, Indirizzo, Lotto, Mandato, MetodoPagamento, Oggetto,
                     PagamentoMandante, Paletta, Soggetto, SpesaMandante)

admin.site.site_header = "Gestionale Asta"
admin.site.site_title = "Gestionale Asta"
admin.site.index_title = "Archivio"

PII_FIELDS = ("nome", "codice_fiscale", "email", "telefono", "documento", "iban")


def vede_pii(request):
    return request.user.has_perm("asta.view_dati_personali")


class IndirizzoInline(admin.StackedInline):
    model = Indirizzo
    extra = 0


@admin.register(Soggetto)
class SoggettoAdmin(admin.ModelAdmin):
    inlines = [IndirizzoInline]
    search_fields = ["codice"]
    list_display = ["codice", "nome_o_mascherato", "tipo", "modificato"]

    @admin.display(description="nome")
    def nome_o_mascherato(self, obj):
        return obj.nome if self._pii else "•••••"

    def changelist_view(self, request, extra_context=None):
        self._pii = vede_pii(request)
        return super().changelist_view(request, extra_context)

    def get_exclude(self, request, obj=None):
        return () if vede_pii(request) else PII_FIELDS

    def get_inlines(self, request, obj):
        return self.inlines if vede_pii(request) else []

    def get_search_results(self, request, queryset, search_term):
        """Ricerca per codice, oppure per CF/email esatti tramite indice cieco."""
        qs, dup = super().get_search_results(request, queryset, search_term)
        if search_term and vede_pii(request):
            from .crypto import blind_index
            h = blind_index(search_term)
            qs = qs | queryset.filter(cf_index=h) | queryset.filter(email_index=h)
        return qs, dup


@admin.register(Mandato)
class MandatoAdmin(admin.ModelAdmin):
    list_display = ["numero", "mandante", "data", "commissione_pct"]
    autocomplete_fields = ["mandante"]
    search_fields = ["numero", "mandante__codice"]


class PalettaInline(admin.TabularInline):
    model = Paletta
    extra = 0
    autocomplete_fields = ["acquirente"]
    fields = ["numero", "acquirente", "indirizzo_spedizione", "spese_spedizione"]


@admin.register(Oggetto)
class OggettoAdmin(admin.ModelAdmin):
    list_display = ("__str__", "categoria", "destinazione", "collocazione", "consegnato_il", "restituito_il")
    list_filter = ("destinazione", "categoria")
    search_fields = ("descrizione", "mandato__numero")
    raw_id_fields = ("mandato",)


@admin.register(Asta)
class AstaAdmin(admin.ModelAdmin):
    list_display = ["numero", "nome", "data", "diritti_pct", "chiusa", "documenti"]

    @admin.display(description="")
    def documenti(self, obj):
        return format_html('<a href="{}">Apri cruscotto →</a>', reverse("asta_cruscotto", args=[obj.pk]))


class IncassoInline(admin.TabularInline):
    model = Incasso
    extra = 1
    fields = ["data", "metodo", "importo", "commissioni", "note"]


@admin.register(Paletta)
class PalettaAdmin(admin.ModelAdmin):
    list_display = ["numero", "asta", "acquirente", "spedizione", "spese_spedizione", "diritti_pct",
                    "stato_consegna", "documenti"]
    list_filter = ["asta", "stato_consegna"]
    inlines = [IncassoInline]
    fieldsets = [
        (None, {"fields": ["asta", "numero", "acquirente", "diritti_pct"]}),
        ("Consegna", {"fields": ["indirizzo_spedizione", "spese_spedizione", "stato_consegna", "note"]}),
        ("Contabilità", {"fields": ["abbuono", "tipo_documento", "numero_documento"]}),
    ]
    autocomplete_fields = ["acquirente"]
    search_fields = ["numero"]

    @admin.display(description="spedizione")
    def spedizione(self, obj):
        return obj.indirizzo_spedizione.citta if obj.indirizzo_spedizione else "ritiro in sede"

    @admin.display(description="documenti")
    def documenti(self, obj):
        return format_html(
            '<a href="{}">Conto</a> · <a href="{}">Spedizione</a>',
            reverse("doc_conto", args=[obj.asta_id, obj.numero]),
            reverse("doc_spedizione", args=[obj.asta_id, obj.numero]))

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "indirizzo_spedizione":
            obj_id = request.resolver_match.kwargs.get("object_id")
            if obj_id:
                p = Paletta.objects.filter(pk=obj_id).first()
                kwargs["queryset"] = Indirizzo.objects.filter(soggetto=p.acquirente) if p else Indirizzo.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Lotto)
class LottoAdmin(admin.ModelAdmin):
    list_display = ["numero", "asta", "descr_breve", "mandato", "commissione_effettiva", "stato"]
    list_filter = ["asta", "stato"]
    search_fields = ["numero", "descrizione", "mandato__numero"]
    autocomplete_fields = ["mandato"]
    actions = ["segna_invenduto", "metti_contovendita", "segna_da_rendere", "segna_reso"]

    @admin.display(description="descrizione")
    def descr_breve(self, obj):
        return obj.descrizione[:60]

    def _cambia(self, request, queryset, stato, **extra):
        qs = queryset.exclude(stato=Lotto.Stato.AGGIUDICATO)
        n = qs.update(stato=stato, **extra)
        if n < queryset.count():
            self.message_user(request, "I lotti aggiudicati non sono stati modificati.", messages.WARNING)
        self.message_user(request, f"{n} lotti aggiornati.")

    @admin.action(description="Segna come invenduto")
    def segna_invenduto(self, request, qs):
        self._cambia(request, qs, Lotto.Stato.INVENDUTO)

    @admin.action(description="Passa in contovendita")
    def metti_contovendita(self, request, qs):
        self._cambia(request, qs, Lotto.Stato.CONTOVENDITA)

    @admin.action(description="Segna da rendere al mandante")
    def segna_da_rendere(self, request, qs):
        self._cambia(request, qs, Lotto.Stato.DA_RENDERE)

    @admin.action(description="Segna come reso (oggi)")
    def segna_reso(self, request, qs):
        self._cambia(request, qs, Lotto.Stato.RESO, data_reso=timezone.localdate())


@admin.register(Aggiudicazione)
class AggiudicazioneAdmin(admin.ModelAdmin):
    list_display = ["lotto", "paletta", "prezzo_martello", "diritti", "registrata_da", "registrata_il"]
    list_filter = ["lotto__asta"]
    raw_id_fields = ["lotto", "paletta"]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.registrata_da = request.user
        super().save_model(request, obj, form, change)


@admin.register(SpesaMandante)
class SpesaMandanteAdmin(admin.ModelAdmin):
    list_display = ["asta", "mandato", "lotto", "descrizione", "importo"]
    list_filter = ["asta"]
    raw_id_fields = ["lotto"]
    autocomplete_fields = ["mandato"]


@admin.register(MetodoPagamento)
class MetodoPagamentoAdmin(admin.ModelAdmin):
    list_display = ["sigla", "descrizione", "contanti", "attivo"]


@admin.register(Incasso)
class IncassoAdmin(admin.ModelAdmin):
    list_display = ["data", "paletta", "metodo", "importo", "commissioni", "note", "registrato_da"]
    list_filter = ["paletta__asta", "metodo", "data"]
    raw_id_fields = ["paletta"]
    date_hierarchy = "data"

    def get_changeform_initial_data(self, request):
        from django.utils import timezone
        d = super().get_changeform_initial_data(request)
        d.setdefault("data", timezone.localdate())
        return d

    def save_model(self, request, obj, form, change):
        if not change:
            obj.registrato_da = request.user
        super().save_model(request, obj, form, change)
        s = obj.paletta.situazione_incasso()
        if s["avviso_contanti"]:
            self.message_user(request, "Attenzione: i contanti per questa paletta raggiungono il limite di "
                                       "legge (5.000 €). Verificare con la contabile.", messages.WARNING)
        if s["residuo"] < 0:
            self.message_user(request, f"Attenzione: per la paletta {obj.paletta.numero} è stato incassato "
                                       f"€ {-s['residuo']} in più del dovuto.", messages.WARNING)
        elif s["residuo"] > 0:
            self.message_user(request, f"Paletta {obj.paletta.numero}: restano da incassare € {s['residuo']}.",
                              messages.INFO)
        else:
            self.message_user(request, f"Paletta {obj.paletta.numero} saldata.")

    def response_add(self, request, obj, post_url_continue=None):
        from django.shortcuts import redirect
        if "_addanother" not in request.POST and "_continue" not in request.POST:
            return redirect("incassi", obj.paletta.asta_id)
        return super().response_add(request, obj, post_url_continue)


@admin.register(PagamentoMandante)
class PagamentoMandanteAdmin(admin.ModelAdmin):
    list_display = ["data", "asta", "mandato", "metodo", "importo", "numero_documento", "note"]
    list_filter = ["asta", "metodo"]
    autocomplete_fields = ["mandato"]
    date_hierarchy = "data"

    def get_changeform_initial_data(self, request):
        from django.utils import timezone
        d = super().get_changeform_initial_data(request)
        d.setdefault("data", timezone.localdate())
        return d

    def response_add(self, request, obj, post_url_continue=None):
        from django.shortcuts import redirect
        if "_addanother" not in request.POST and "_continue" not in request.POST:
            return redirect("pagamenti_mandanti", obj.asta_id)
        return super().response_add(request, obj, post_url_continue)
